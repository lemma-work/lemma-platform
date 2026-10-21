"""Telling the pod's members a contact asked to stop.

An opt-out is kept by the handle's hash (``contacts``), and holds whatever
the members do; but a member writing to the contact afterwards should not
learn of it only from their own line ending ``SKIPPED``. So a contact who
writes STOP leaves a note in their channel, for the pod's members only --
never carried to the contact -- naming whoever answers them, so they hear of
it outside the app too.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.channels.contracts import (
    ChannelMessage,
    NewLine,
    Principal,
    Sender,
    assignee_of,
    contact_channel,
    post,
)

logger = get_logger(__name__)

#: How a member names where the contact asked to stop.
_WHERE = {
    "RESEND": "email",
    "WHATSAPP": "WhatsApp",
    "TELEGRAM": "Telegram",
    "SLACK": "Slack",
    "TEAMS": "Teams",
}


async def note_opt_out(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID, platform: str
) -> ChannelMessage | None:
    """A members-only line in the contact's channel: they asked to stop on
    ``platform``, and nothing goes to them there until they write again."""
    channel = await contact_channel(uow, pod_id=pod_id, contact_id=contact_id)
    assignee = await assignee_of(uow, channel.id)
    line = await post(
        uow,
        channel_id=channel.id,
        line=NewLine(
            sender=Sender.agent(
                effective_agent_id(None, pod_id=pod_id),
                on_behalf_of=Principal.contact(contact_id),
            ),
            text=(
                f"They asked to stop getting messages by {_WHERE.get(platform, platform)}. "
                "Nothing more is sent to them there until they write again."
            ),
            members_only=True,
        ),
        mentions=[Principal.member(assignee)] if assignee is not None else (),
    )
    logger.info(
        "agent_surfaces.opt_out_notes.noted.observed",
        channel_id=str(channel.id),
        platform=platform,
    )
    return line


__all__ = ["note_opt_out"]
