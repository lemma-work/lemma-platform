"""The member who looks after a bot's contacts, and what happens when they leave.

A member who leaves the pod looks after nothing: the contact path stops
answering contacts on that bot (``ContactDoor``) rather than pass their
questions to somebody who can no longer act on them. That is the safe answer
and a silent one, so the pod's administrators are told when it happens, and the
bot's settings say so until somebody else takes the contacts on.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import (
    move_contact_conversation,
)
from app.modules.agent.contracts.conversations_for_surfaces import SurfaceConversation
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.domain.surface_config import ContactAnswer
from app.modules.agent_surfaces.domain.web_widgets import WidgetAnswer
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)
from app.modules.pod.contracts.members import pod_administrators, pod_member_id

logger = get_logger(__name__)

#: What a surface says while nobody in the pod looks after its contacts.
UNATTENDED_WARNING = (
    "The member who looked after this bot's contacts is no longer in the space, "
    "so it is not answering contacts. Choose someone else to look after them."
)

#: The most surfaces of one pod read when a member leaves.
_MAX_SURFACES = 500


def _keeper(surface: AgentSurfaceEntity) -> UUID | None:
    policy = surface.config.contacts
    return policy.looked_after_by if policy.answer is not ContactAnswer.OFF else None


async def contacts_warnings(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, *surfaces: AgentSurfaceEntity
) -> dict[UUID, str]:
    """The warning for each of these that answers contacts nobody looks after."""
    keepers = {keeper for surface in surfaces if (keeper := _keeper(surface))}
    present = {
        keeper
        for keeper in keepers
        if await pod_member_id(uow, pod_id, keeper) is not None
    }
    return {
        surface.id: UNATTENDED_WARNING
        for surface in surfaces
        if (keeper := _keeper(surface)) is not None and keeper not in present
    }


async def tell_admins_a_keeper_left(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID
) -> int:
    """Tell the pod's administrators which bots this member looked after.

    Returns how many were told; none when the member looked after nothing.
    """
    surfaces, _ = await SurfaceRepository(uow).list_by_pod(pod_id, limit=_MAX_SURFACES)
    names = [surface.name for surface in surfaces if _keeper(surface) == user_id]
    names += [
        widget.name
        for widget in await WebWidgetRepository(uow.session).list(pod_id=pod_id)
        if widget.looked_after_by == user_id and widget.answer is not WidgetAnswer.OFF
    ]
    if not names:
        return 0
    admins = [
        admin
        for admin in await pod_administrators(uow, pod_id)
        if admin.user_id != user_id
    ]
    notes = NotificationRepository(uow)
    for admin in admins:
        await notes.create(
            NotificationEntity(
                pod_id=pod_id,
                recipient_user_id=admin.user_id,
                recipient_pod_member_id=admin.pod_member_id,
                origin_kind=NotificationOriginKind.API,
                title="Nobody is looking after contacts",
                body=(
                    "The member who looked after contacts on "
                    + ", ".join(sorted(names))
                    + " has left the space, so those bots have stopped answering "
                    "contacts. Choose someone else to look after them in each "
                    "bot's settings."
                ),
                expects_response=False,
                delivery_status=NotificationDeliveryStatus.UNDELIVERABLE,
            )
        )
    logger.info(
        "agent_surfaces.contact_keepers.keeper_left.observed",
        pod_id=str(pod_id),
        surfaces=len(names),
        admins_told=len(admins),
    )
    return len(admins)


async def owned_or_moved(
    uow: SqlAlchemyUnitOfWork,
    conversation: SurfaceConversation,
    user_id: UUID,
    *,
    contact_id: UUID | None,
) -> bool:
    """Whether ``user_id`` owns the conversation, once a contact's has moved.

    A contact's conversation is found by its ``~contact:`` link and is the
    contact's, whoever looks after contacts now: when that member changes, it
    moves to the new one with its history rather than being left behind for a
    fresh one. Every other conversation is only ever its owner's.
    """
    if conversation.user_id == user_id:
        return True
    if contact_id is None or conversation.audience.contact_id != contact_id:
        return False
    moved = await move_contact_conversation(
        uow,
        conversation_id=conversation.id,
        contact_id=contact_id,
        to_user_id=user_id,
    )
    if moved:
        logger.info(
            "agent_surfaces.contact_keepers.contact_conversation_moved.observed",
            conversation_id=str(conversation.id),
        )
    return moved
