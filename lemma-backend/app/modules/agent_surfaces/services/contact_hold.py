"""Whether a member, not an agent, answers a contact now.

Two ways, both on the contact's channel (``channels``) and its current case:

* **The channel's agents are off** (``agents_answer``). An operator answering
  leads by hand turns them off; nothing answers the contact but a member
  until somebody turns them back on.
* **A member stepped in** by writing to the contact: they are its case's
  assignee.
  Their agent stays quiet while the member is in the conversation. An
  assignee who has not written in ``hand_back_after_hours`` -- counted from
  being assigned, until they first write -- has let go: the
  contact's next message is the agent's again, and the member is told, once.
  Letting go is one conditional write, so two messages arriving together
  cannot hand it back twice, and a member who writes just then keeps it.

Turning the agents on is also how a member hands a contact back at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts.channel_conversations import (
    channel_of_conversation,
)
from app.modules.agent_surfaces.services.direct_messages import tell_member
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    channel_by_id,
    hand_back,
)

logger = get_logger(__name__)


class MemberHold(StrEnum):
    """Why a member answers this contact rather than an agent."""

    #: The channel's agents are switched off.
    AGENTS_OFF = "agents_off"
    #: A member is in the conversation now.
    STEPPED_IN = "stepped_in"


async def held_by_a_member(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, now: datetime | None = None
) -> MemberHold | None:
    """Whether a member holds this contact's conversation; a member who has
    gone quiet past the channel's window is let go here, and told."""
    channel_id = await channel_of_conversation(uow, conversation_id)
    channel = await channel_by_id(uow, channel_id) if channel_id else None
    if channel is None or channel.kind is not ChannelKind.CONTACT:
        return None
    if not channel.agents_answer:
        return MemberHold.AGENTS_OFF
    member = channel.assignee_user_id
    if member is None:
        return None
    # From their last line there, or from being assigned if they have not
    # written yet: a member just handed a contact has the whole window.
    last = channel.assignee_last_line_at or channel.assigned_at
    window = timedelta(hours=channel.hand_back_after_hours)
    at = now or datetime.now(timezone.utc)
    if last is not None and at - last < window:
        return MemberHold.STEPPED_IN
    if not await hand_back(
        uow, channel_id=channel.id, user_id=member, quiet_since=at - window
    ):
        # They wrote meanwhile (they hold it still), or another turn let them
        # go a moment ago and told them.
        fresh = await channel_by_id(uow, channel.id)
        if fresh is not None and fresh.assignee_user_id == member:
            return MemberHold.STEPPED_IN
        return None
    await _tell_let_go(uow, channel, member)
    return None


async def _tell_let_go(
    uow: SqlAlchemyUnitOfWork, channel: Channel, member: UUID
) -> None:
    await tell_member(
        uow,
        pod_id=channel.pod_id,
        user_id=member,
        text=(
            "A contact you were answering wrote again, and you had not written "
            f"to them in {channel.hand_back_after_hours} hours, so the "
            "agent answers them again. Their channel is in Contacts: write there "
            "to step back in, or switch its agent off to answer them yourself."
        ),
    )
    logger.info(
        "agent_surfaces.contact_hold.handed_back.observed",
        channel_id=str(channel.id),
        after_hours=channel.hand_back_after_hours,
    )
