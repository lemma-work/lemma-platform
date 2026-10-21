"""How far a member has read a channel and its threads, and the mentions that
reading clears. Mixed into ``MemberRepository``, which owns the seats it reads."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.live.signals import signal_after_commit
from app.modules.channels.infrastructure.models import (
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelReadStateModel,
)
from app.modules.channels.infrastructure.revisions import (
    read_signal,
)


class ReadState:
    session: AsyncSession

    async def mark_read(self, *, channel_id: UUID, user_id: UUID, seq: int) -> int:
        """They have read the timeline up to ``seq``; it never goes back. What
        named them in a line of the timeline up to there is read; what named
        them in a thread is not -- that is read in the thread
        (``mark_thread_read``). Returns where they are now."""
        state, line = ChannelReadStateModel, ChannelMessageModel
        pod_id = await self.session.scalar(
            select(ChannelModel.pod_id).where(ChannelModel.id == channel_id)
        )
        if pod_id is None:
            return 0
        await self.session.execute(
            insert(state)
            .values(channel_id=channel_id, pod_id=pod_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id", "channel_id"])
        )
        top = select(ChannelModel.last_seq).where(ChannelModel.id == channel_id)
        upto = func.least(seq, top.scalar_subquery())
        # The replies among the lines newly read, from the row as it is when
        # the update takes it: a read racing this one is counted once.
        replies = (
            select(func.count())
            .where(
                line.channel_id == channel_id,
                line.seq > state.last_read_seq,
                line.seq <= upto,
                line.thread_root_id.is_not(None),
            )
            .scalar_subquery()
        )
        moved = (
            await self.session.execute(
                update(state)
                .where(
                    state.user_id == user_id,
                    state.channel_id == channel_id,
                    state.last_read_seq < upto,
                )
                .values(
                    last_read_seq=upto,
                    read_replies=state.read_replies + replies,
                    updated_at=func.now(),
                )
                # Whether anything naming them is now read: the lines are
                # visited only then, not on every read.
                .returning(
                    state.last_read_seq,
                    _unread_mentions(
                        pod_id, channel_id, user_id, upto=state.last_read_seq
                    ),
                )
            )
        ).first()
        if moved is None:
            # Not moved -- they were already there, or past it. A line that
            # named them before their number got there (they joined after it,
            # or were named by an edit) is still read by reading to it.
            read_seq, unread = await self._read_seq_and_unread(
                pod_id=pod_id, channel_id=channel_id, user_id=user_id, upto=seq
            )
        else:
            read_seq, unread = int(moved[0]), bool(moved[1])
        if unread:
            await self._read_mentions(
                pod_id=pod_id,
                channel_id=channel_id,
                user_id=user_id,
                upto=min(seq, read_seq),
                where=line.thread_root_id.is_(None),
            )
        signal_after_commit(self.session, pod_id, read_signal(channel_id, user_id))
        return read_seq

    async def mark_thread_read(
        self, *, channel_id: UUID, user_id: UUID, root_id: UUID, seq: int
    ) -> None:
        """They have read a thread -- its first line and its replies -- up to
        ``seq``: what named them there is read. The timeline's read number
        does not move."""
        pod_id = await self.session.scalar(
            select(ChannelModel.pod_id).where(ChannelModel.id == channel_id)
        )
        if pod_id is None:
            return
        if not await self.session.scalar(
            select(_unread_mentions(pod_id, channel_id, user_id, upto=literal(seq)))
        ):
            return
        read = await self._read_mentions(
            pod_id=pod_id,
            channel_id=channel_id,
            user_id=user_id,
            upto=seq,
            where=or_(
                ChannelMessageModel.thread_root_id == root_id,
                ChannelMessageModel.id == root_id,
            ),
        )
        if read:
            signal_after_commit(self.session, pod_id, read_signal(channel_id, user_id))

    async def _read_mentions(
        self,
        *,
        pod_id: UUID,
        channel_id: UUID,
        user_id: UUID,
        upto: int,
        where: ColumnElement[bool],
    ) -> int:
        """Mark read the member's unread mentions in the channel up to
        ``upto``, in the lines ``where`` picks: along the unread range of
        ``ix_channel_mentions_user``. How many were."""
        mention, line = ChannelMentionModel, ChannelMessageModel
        result = await self.session.execute(
            update(mention)
            .where(
                mention.user_id == user_id,
                mention.pod_id == pod_id,
                mention.channel_id == channel_id,
                mention.seq <= upto,
                mention.read_at.is_(None),
                line.id == mention.message_id,
                where,
            )
            .values(read_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def _read_seq_and_unread(
        self, *, pod_id: UUID, channel_id: UUID, user_id: UUID, upto: int
    ) -> tuple[int, bool]:
        """Where they have read to, and whether anything up to ``upto`` naming
        them there is unread: their row by key and one probe of the mention
        index."""
        state = ChannelReadStateModel
        found = (
            await self.session.execute(
                select(
                    state.last_read_seq,
                    _unread_mentions(
                        pod_id,
                        channel_id,
                        user_id,
                        upto=func.least(literal(upto), state.last_read_seq),
                    ),
                ).where(state.channel_id == channel_id, state.user_id == user_id)
            )
        ).first()
        if found is None:
            return 0, False
        return int(found[0]), bool(found[1])

    async def read_seq(self, *, channel_id: UUID, user_id: UUID) -> int:
        found = await self.session.scalar(
            select(ChannelReadStateModel.last_read_seq).where(
                ChannelReadStateModel.channel_id == channel_id,
                ChannelReadStateModel.user_id == user_id,
            )
        )
        return int(found or 0)

    async def read_seqs(
        self, *, channel_ids: Collection[UUID], user_id: UUID
    ) -> dict[UUID, int]:
        if not channel_ids:
            return {}
        rows = await self.session.execute(
            select(
                ChannelReadStateModel.channel_id, ChannelReadStateModel.last_read_seq
            ).where(
                ChannelReadStateModel.user_id == user_id,
                ChannelReadStateModel.channel_id.in_(sorted(channel_ids)),
            )
        )
        return {row[0]: int(row[1]) for row in rows}


def _unread_mentions(
    pod_id: UUID,
    channel_id: UUID,
    user_id: UUID,
    *,
    upto: ColumnElement[int] | InstrumentedAttribute[int],
) -> ColumnElement[bool]:
    """Whether anything up to ``upto`` naming the member in the channel is
    unread: one probe of ``ix_channel_mentions_user``, which holds only the
    unread -- never the lines."""
    mention = ChannelMentionModel
    return (
        select(mention.id)
        .where(
            mention.user_id == user_id,
            mention.pod_id == pod_id,
            mention.channel_id == channel_id,
            mention.seq <= upto,
            mention.read_at.is_(None),
        )
        .exists()
    )
