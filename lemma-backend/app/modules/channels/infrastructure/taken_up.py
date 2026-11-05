"""A platform message already a line, taken up by the turn that answers it.

A group's message is written as it arrives (``lines.LineRepository.append``),
before anything decides who answers it, and perhaps before anybody could name
who said it. The turn that takes it up knows both, and says so on that line
rather than writing it again: the conversation answering it, and -- for a line
said by nobody named -- whom the turn resolved. Each only where the line did
not know it, so the first conversation to take it up keeps it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import ChannelMessage, Sender
from app.modules.channels.infrastructure.lines import heard_row
from app.modules.channels.infrastructure.models import ChannelMessageModel
from app.modules.channels.infrastructure.revisions import next_rev
from app.modules.channels.infrastructure.rows import (
    message_entity,
    nameless,
    sender_values,
)


async def answered_in(
    session: AsyncSession,
    source_id: UUID,
    external_message_id: str,
    *,
    conversation_id: UUID,
    sender: Sender,
) -> ChannelMessage | None:
    """The line, now answered in ``conversation_id`` and said by ``sender``
    where it could not say who. ``None`` for a message that is no line here.
    A nameless speaker keeps the name the line had for them, if it had one:
    what the place saw them called."""
    row = await heard_row(session, source_id, external_message_id)
    if row is None:
        return None
    if row.conversation_id is not None and not _names_them(row, sender):
        return message_entity(row)
    # Who said it, or who answers it, is what its readers see: they are sent
    # it again. The channel's number is taken before the line is written, the
    # order ``revisions`` keeps -- and so its lock before the line's, as an
    # edit takes them.
    await next_rev(session, row.channel_id, message_id=row.id)
    row = await heard_row(session, source_id, external_message_id, locked=True)
    if row is None:
        return None
    if row.conversation_id is None:
        row.conversation_id = conversation_id
    if _names_them(row, sender):
        values = sender_values(sender)
        values["sender_name"] = row.sender_name or values["sender_name"]
        for key, value in values.items():
            setattr(row, key, value)
    await session.flush()
    return message_entity(row)


def _names_them(row: ChannelMessageModel, sender: Sender) -> bool:
    """The line was said by nobody named, and ``sender`` is somebody."""
    return nameless(row) and bool(sender.user_id or sender.contact_id)
