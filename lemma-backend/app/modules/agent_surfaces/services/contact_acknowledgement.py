"""Telling a contact a person will reply, when no agent will.

A contact whose channel has its agents switched off, or who was handed to a
member who has not written to them yet, is answered by a member, whenever one
gets to it. Until then the pod may say so once -- its own line,
set in the pod's settings (``config.contacts.auto_acknowledge``), for example
"Thanks, someone from Acme will reply". Once per *wait*: from the contact's
first unanswered message until a member writes to them, or their case is
closed. Two messages arriving together say it once (a claim on the contact's
case under its channel's lock, ``channels.contracts.acknowledge``).

The line is the pod's agent's, on the contact's timeline at the place they
wrote from, and goes there as a delivery like any other line. A contact who
asked the pod to stop is not written to: the outreach rules are asked first,
and a refusal claims nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.channel_conversations import (
    channel_of_conversation,
)
from app.modules.agent_surfaces.services.channel_lines import WEB, shown_on_web
from app.modules.agent_surfaces.services.contact_reach import reach_for
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    NewLine,
    Principal,
    Sender,
    Source,
    acknowledge,
    channel_by_id,
    post,
    sources_of,
)
from app.modules.contacts.contracts.outreach import OutreachKind, may_send
from app.modules.pod.contracts.contact_settings import pod_auto_acknowledgement

logger = get_logger(__name__)


async def acknowledge_contact(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    now: datetime | None = None,
) -> str | None:
    """Say the pod's acknowledgement to this contact, if it is due: no agent
    answers them -- their channel's agents are off, or a member they have not
    heard from yet holds them -- the pod set one, it is the first of this
    wait, and they may be written to. Returns what was said -- for their
    conversation, which is where a web chat shows it -- or ``None``."""
    text = await pod_auto_acknowledgement(uow.session, pod_id)
    if text is None:
        return None
    channel_id = await channel_of_conversation(uow, conversation_id)
    channel = await channel_by_id(uow, channel_id) if channel_id else None
    if (
        channel is None
        or channel.kind is not ChannelKind.CONTACT
        or channel.contact_id is None
        or (channel.agents_answer and not _handed_to_a_member(channel))
    ):
        return None
    source = _where_they_wrote(await sources_of(uow, channel.id))
    if source is None:
        return None
    if not await _may_write(uow, channel, source, at=now or datetime.now(timezone.utc)):
        return None
    if not await acknowledge(uow, channel.id):
        return None
    line = await post(
        uow,
        channel_id=channel.id,
        line=NewLine(
            sender=Sender.agent(
                await _speaker(uow, conversation_id, pod_id=pod_id),
                on_behalf_of=Principal.contact(channel.contact_id),
            ),
            text=text,
            target_source_id=source.id,
            conversation_id=conversation_id,
        ),
        deliver_to=[source.id],
    )
    if line is not None and source.platform == WEB:
        # A web chat shows it from their conversation; nothing carries it.
        await shown_on_web(uow, line, source)
    logger.info(
        "agent_surfaces.contact_acknowledgement.sent.observed",
        channel_id=str(channel.id),
        platform=source.platform,
    )
    return text


def _handed_to_a_member(channel: Channel) -> bool:
    """A member holds the contact and has not written to them yet: the agent
    is quiet, and nobody has said anything. A member in the conversation is
    answering them, and is not announced on every line."""
    return (
        channel.assignee_user_id is not None and channel.assignee_last_line_at is None
    )


def _where_they_wrote(sources: list[Source]) -> Source | None:
    """The place the contact was heard from last."""
    heard = [
        source
        for source in sources
        if not source.is_pending and source.last_inbound_at is not None
    ]
    return heard[0] if heard else None


async def _may_write(
    uow: SqlAlchemyUnitOfWork, channel: Channel, source: Source, *, at: datetime
) -> bool:
    if source.platform == WEB:
        return True
    reach = reach_for(
        source.platform,
        address=source.external_user_id,
        last_inbound_at=source.last_inbound_at,
    )
    if reach is None or channel.contact_id is None:
        return False
    address, window = reach
    verdict = await may_send(
        uow,
        pod_id=channel.pod_id,
        contact_id=channel.contact_id,
        to=address,
        kind=OutreachKind.ACK,
        at=at,
        window=window,
    )
    if not verdict.allowed:
        logger.info(
            "agent_surfaces.contact_acknowledgement.refused.observed",
            channel_id=str(channel.id),
            code=verdict.code,
        )
    return verdict.allowed


async def _speaker(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, pod_id: UUID
) -> UUID:
    """The agent of the contact's conversation: the one that would have answered."""
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    return effective_agent_id(
        conversation.agent_id if conversation is not None else None, pod_id=pod_id
    )
