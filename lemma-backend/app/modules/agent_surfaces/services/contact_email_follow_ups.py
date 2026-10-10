"""A follow-up by email to a contact who has no email thread with the pod.

A web chat visitor who proved an address with a one-time code is a contact
with a verified email handle, but their conversation is the chat: there is no
thread from the pod's address to reply into. Asked for explicitly
(``channel="email"``), this opens one from the pod's own email surface -- the
cold-open send notifications use -- and links it the way an inbound email from
the contact would be linked (``~contact:{id}``), so their reply binds to the
same conversation, as the same contact.

Sent first, then recorded: the conversation and its link are written only for
a thread that exists, since a link to a thread nobody sent would swallow the
reply. A send that failed leaves nothing here; the caller records it, as not
sent, in the contact's latest conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.delegation import agent_display_name
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.contact_conversations import append_follow_up
from app.modules.agent_surfaces.composition import build_surface_egress
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
)
from app.modules.agent_surfaces.domain.groups import contact_link_user
from app.modules.agent_surfaces.domain.ports import ColdEmailThread
from app.modules.agent_surfaces.domain.surface_config import ContactAnswer
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (  # noqa: E501
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.agent_naming import agent_name_for_surface
from app.modules.agent_surfaces.services.cold_email_thread import (
    follow_up_seed_id,
)
from app.modules.pod.contracts.members import pod_member_id

logger = get_logger(__name__)

#: A subject is the message's first line, cut to what an inbox list shows.
MAX_SUBJECT_CHARS = 78


@dataclass(frozen=True, slots=True)
class ContactsMailbox:
    """The pod's email surface that answers contacts, and who looks after them."""

    surface: AgentSurfaceEntity
    looked_after_by: UUID


async def contacts_mailbox(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID
) -> ContactsMailbox | None:
    """The email surface a contact's reply would be answered on, if the pod has one.

    Active, set to answer contacts, and looked after by somebody still in the
    pod: a reply to a surface with contacts off would be refused as a
    stranger's, and one nobody looks after would be answered by nobody. The
    oldest such surface, so the choice does not change between follow-ups.
    """
    surfaces, _ = await SurfaceRepository(uow).list_by_pod(pod_id)
    for surface in surfaces:
        policy = surface.config.contacts
        if (
            not surface.is_active
            or not surface.surface_type.is_email
            or policy.answer is ContactAnswer.OFF
            or policy.looked_after_by is None
        ):
            continue
        if await pod_member_id(uow, pod_id, policy.looked_after_by) is None:
            continue
        return ContactsMailbox(surface, policy.looked_after_by)
    return None


def follow_up_subject(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first) <= MAX_SUBJECT_CHARS:
        return first
    return first[: MAX_SUBJECT_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


async def email_contact(
    uow_factory: UnitOfWorkFactory,
    mailbox: ContactsMailbox,
    *,
    contact_id: UUID,
    recipient_email: str,
    text: str,
    sent_by_user_id: UUID,
) -> UUID | None:
    """Email the contact from the pod's address; the new conversation, or None.

    ``None`` means it was not sent: the platform refused it, or cannot start
    a thread it has no prior message for.
    """
    surface = mailbox.surface
    subject = follow_up_subject(text)
    try:
        async with uow_factory() as uow:
            thread = await build_surface_egress(uow).open_cold_email_thread(
                surface=surface,
                recipient_email=recipient_email,
                subject=subject,
                message=text,
                thread_seed_id=follow_up_seed_id(surface),
                metadata={
                    "agent_display_name": agent_display_name(
                        await agent_name_for_surface(uow, surface)
                    )
                },
            )
    except PLATFORM_TRANSPORT_ERRORS as exc:
        logger.warning(
            "agent_surfaces.contact_follow_ups.email_failed.degraded",
            error_type=type(exc).__name__,
        )
        thread = None
    logger.info(
        "agent_surfaces.contact_follow_ups.email_opened.observed",
        delivered=thread is not None,
    )
    if thread is None:
        return None
    async with uow_factory() as uow:
        conversation_id = await _open_contacts_thread(
            uow, mailbox, thread, contact_id=contact_id, title=subject
        )
        await append_follow_up(
            uow,
            conversation_id=conversation_id,
            message=text,
            sent_by_user_id=sent_by_user_id,
            delivered=True,
        )
    return conversation_id


async def _open_contacts_thread(
    uow: SqlAlchemyUnitOfWork,
    mailbox: ContactsMailbox,
    thread: ColdEmailThread,
    *,
    contact_id: UUID,
    title: str,
) -> UUID:
    """The contact's conversation for this thread, linked as their reply finds it.

    Owned by the member who looks after the surface's contacts and marked as
    the contact's, as ``ContactDoor`` opens one for an inbound email; the
    link's key is the contact's, not the address, because that is the key the
    reply is bound under.
    """
    surface = mailbox.surface
    link_user = contact_link_user(contact_id)
    conversation = await agent_conversations.open_surface_conversation(
        uow,
        pod_id=surface.pod_id,
        agent_name=await agent_name_for_surface(uow, surface),
        user_id=mailbox.looked_after_by,
        title=title,
        for_contact=contact_id,
        metadata={
            "source": "contact_follow_up",
            "surface_id": str(surface.id),
            "surface_platform": surface.surface_type.value,
            "external_channel_id": thread.external_channel_id,
            "external_thread_id": thread.external_thread_id,
            "external_user_id": link_user,
            "conversation_kind": "EMAIL",
        },
        # Nobody asked to run the agent: the member wrote, and the conversation
        # is the looked-after-by member's, as an inbound one would be.
        require_execute_grant=False,
    )
    await SurfaceConversationLinkRepository(uow).create(
        AgentSurfaceConversationLink(
            surface_id=surface.id,
            conversation_id=conversation.id,
            platform=surface.surface_type.value,
            external_channel_id=thread.external_channel_id,
            external_thread_id=thread.external_thread_id,
            external_user_id=link_user,
            routed_agent_id=surface.agent_id,
            conversation_kind="EMAIL",
            route_key="email",
            last_event=thread.last_event,
            last_message_id=thread.external_message_id,
            # They have not written on this thread; an outbound is not inbound.
            last_inbound_at=None,
        )
    )
    return conversation.id


__all__ = ["ContactsMailbox", "contacts_mailbox", "email_contact"]
