"""A member reading a channel: its timeline, or one of its threads.

One rule for ``POST /channels/{c}/read`` and the socket's ``read`` call. The
timeline is read to a line: its number moves, and what named the member in
its top-level lines up to there is read. A thread is read to a line: what
named them there is read, and the timeline's number stays where it was.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository


class NotInChannel(LookupError):
    """The thread named is not one of this channel's."""


async def read_to(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    *,
    user_id: UUID,
    seq: int,
    thread_root_id: UUID | None = None,
) -> int:
    """Read the timeline -- or, with ``thread_root_id``, that thread -- up
    to ``seq``. Where the member's timeline read number is now."""
    members = MemberRepository(uow.session)
    if thread_root_id is None:
        return await members.mark_read(channel_id=channel.id, user_id=user_id, seq=seq)
    line = await LineRepository(uow.session).get(thread_root_id)
    if line is None or line.channel_id != channel.id:
        raise NotInChannel(str(thread_root_id))
    await members.mark_thread_read(
        channel_id=channel.id,
        user_id=user_id,
        root_id=line.thread_root_id or line.id,
        seq=seq,
    )
    return await members.read_seq(channel_id=channel.id, user_id=user_id)


__all__ = ["NotInChannel", "read_to"]
