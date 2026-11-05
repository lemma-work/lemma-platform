"""A channel's change counter and change log, and the signal that tells its
live hubs.

Every change a channel's live subscribers are shown takes the next number of
the channel (``channels.last_rev``) and appends one row to its change log
(``channel_changes``): the line it moved, or none for the channel's own fields
or members (which also moves ``changed_rev``). A line is never rewritten just
to say it moved. The number is taken by updating the channel row, which holds
its lock until the transaction ends, so numbers commit in order and the log
has no gaps: a hub that has read up to ``n`` and finds ``n + 2`` committed
will never later find ``n + 1``. A hub reads ``rev > n`` and is exact, however
many signals it missed -- back to the oldest row kept (``prune_changes``).

The counter is taken *before* the line or question it covers is touched, the
same order a new line takes the channel lock in (``LineRepository.append``),
so two writers never wait on each other's rows the other way round.

While a line is being posted (``holding``) the channel's row is already
locked, and every change the post makes -- the line, its thread's root, a
seat, a case moving -- is counted in memory and written at the end as one
``UPDATE channels`` and one insert into the log.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Boolean, Select, bindparam, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live.signals import signal_after_commit
from app.modules.channels.infrastructure.change_models import ChannelChangeModel
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import ChannelModel

#: How long a change is kept: a subscriber resuming from further back is told
#: to refetch (``resync``) instead. Longer than any client is expected to stay
#: away and still resume.
CHANGE_RETENTION = timedelta(days=14)

_HELD = "channels.held_heads"


def channel_signal(channel_id: UUID) -> dict[str, str]:
    """A channel's change counter moved: read it."""
    return {"k": "channel", "c": str(channel_id)}


def read_signal(channel_id: UUID, user_id: UUID) -> dict[str, str]:
    """A member's read position in a channel moved: read it."""
    return {"k": "read", "c": str(channel_id), "u": str(user_id)}


# -- a post: the channel's row held, its changes written once -------------------


@dataclass(slots=True)
class HeldHead:
    """A channel's head as a post holds it: its row locked, its changes
    counted here and written once, when the post is done."""

    channel_id: UUID
    pod_id: UUID
    last_seq: int
    last_rev: int
    #: The channel's kind, and whether a group shows its agents' answers to
    #: everybody in it: what a line is posted as (``shared_when_posted``).
    kind: str
    answers_shared: bool
    #: How many of its lines are replies in a thread.
    replies: int = 0
    #: Each change taken, in order: the line it moved, or ``None``.
    changes: list[UUID | None] = field(default_factory=list)
    channel_changed: bool = False
    #: Other columns of the channel row the post sets.
    values: dict[str, object] = field(default_factory=dict)
    #: A top-level line's time: the channel's last line, if newer.
    line_at: datetime | None = None

    def take(
        self, message_id: UUID | None = None, *, channel_changed: bool = False
    ) -> int:
        self.changes.append(message_id)
        self.channel_changed = self.channel_changed or channel_changed
        return self.rev

    @property
    def rev(self) -> int:
        return self.last_rev + len(self.changes)

    def channel_values(self) -> dict[str, object]:
        values: dict[str, object] = dict(self.values)
        if self.changes:
            values["last_rev"] = self.rev
        if self.channel_changed:
            values["changed_rev"] = self.rev
        if self.line_at is not None:
            values["last_line_at"] = func.greatest(
                ChannelModel.last_line_at, self.line_at
            )
        return values


def _held(session: AsyncSession) -> dict[UUID, HeldHead | None]:
    held: dict[UUID, HeldHead | None] = session.info.setdefault(_HELD, {})
    return held


def _held_head(session: AsyncSession, channel_id: UUID) -> HeldHead | None:
    return session.info.get(_HELD, {}).get(channel_id)


@asynccontextmanager
async def holding(session: AsyncSession, channel_id: UUID) -> AsyncIterator[None]:
    """A post of a line to the channel: from its ``hold`` to the end of the
    block, its changes are counted, then written in one ``UPDATE channels``
    and one insert into the log. Nothing is written if the block fails."""
    held = _held(session)
    if channel_id in held:
        raise RuntimeError("A post to this channel is already under way")
    held[channel_id] = None
    try:
        yield
        head = held.get(channel_id)
        if head is not None:
            await _write_head(session, head)
    finally:
        held.pop(channel_id, None)


async def hold(session: AsyncSession, channel_id: UUID) -> HeldHead | None:
    """Lock the channel's row for the post ``holding`` it; ``None`` for no
    such channel."""
    held = _held(session)
    if channel_id not in held:
        raise RuntimeError("hold() outside holding()")
    existing = held[channel_id]
    if existing is not None:
        return existing
    row = (
        await session.execute(
            select(
                ChannelModel.pod_id,
                ChannelModel.last_seq,
                ChannelModel.last_rev,
                ChannelModel.replies,
                ChannelModel.kind,
                ChannelModel.answers_shared,
            )
            .where(ChannelModel.id == channel_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        return None
    head = HeldHead(
        channel_id=channel_id,
        pod_id=row[0],
        last_seq=int(row[1]),
        last_rev=int(row[2]),
        replies=int(row[3]),
        kind=str(row[4]),
        answers_shared=bool(row[5]),
    )
    held[channel_id] = head
    return head


async def _write_head(session: AsyncSession, head: HeldHead) -> None:
    values = head.channel_values()
    if not values:
        return
    await session.execute(
        update(ChannelModel)
        .where(ChannelModel.id == head.channel_id)
        .values(**values)
        # Kept in step with the session: the post's readers see the new head.
        .execution_options(synchronize_session="fetch")
    )
    if head.changes:
        await session.execute(
            insert(ChannelChangeModel),
            [
                {
                    "channel_id": head.channel_id,
                    "rev": head.last_rev + n,
                    "message_id": message_id,
                    "pod_id": head.pod_id,
                }
                for n, message_id in enumerate(head.changes, start=1)
            ],
        )
        signal_after_commit(session, head.pod_id, channel_signal(head.channel_id))


async def set_on_head(
    session: AsyncSession, channel_id: UUID, **values: object
) -> None:
    """Set columns of the channel row: with the post's own write when one is
    under way, else in one statement. Says nothing to subscribers by itself
    (``touch_channel`` does)."""
    head = _held_head(session, channel_id)
    if head is not None:
        head.values.update(values)
        return
    await session.execute(
        update(ChannelModel)
        .where(ChannelModel.id == channel_id)
        .values(**values)
        # Kept in step with the session: the post's readers see the new head.
        .execution_options(synchronize_session="fetch")
    )


# -- one change, outside a post -------------------------------------------------

#: The channel's next numbers, taken in its row's lock, and their rows of the
#: log, in one statement. ``changed`` also moves ``changed_rev``.
_TAKE = text(
    """
    WITH bumped AS (
        UPDATE channels
        SET last_rev = channels.last_rev + cardinality(:ids),
            changed_rev = CASE WHEN :changed THEN channels.last_rev
                + cardinality(:ids) ELSE channels.changed_rev END
        WHERE channels.id = :channel_id
        RETURNING channels.id, channels.pod_id, channels.last_rev
    )
    INSERT INTO channel_changes (channel_id, rev, message_id, pod_id)
    SELECT bumped.id, bumped.last_rev - cardinality(:ids) + line.n,
           line.message_id, bumped.pod_id
    FROM bumped, unnest(:ids) WITH ORDINALITY AS line (message_id, n)
    RETURNING channel_changes.pod_id, channel_changes.rev
    """
).bindparams(
    bindparam("channel_id", type_=PG_UUID(as_uuid=True)),
    bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True))),
    bindparam("changed", type_=Boolean),
)


async def _take(
    session: AsyncSession,
    channel_id: UUID,
    message_ids: Sequence[UUID | None],
    *,
    changed: bool = False,
) -> int | None:
    """Take a number per entry of ``message_ids``; the last one, or ``None``
    for no such channel."""
    head = _held_head(session, channel_id)
    if head is not None:
        for message_id in message_ids:
            head.take(message_id, channel_changed=changed)
        return head.rev
    rows = (
        await session.execute(
            _TAKE,
            {"channel_id": channel_id, "ids": list(message_ids), "changed": changed},
        )
    ).all()
    if not rows:
        return None
    signal_after_commit(session, rows[0][0], channel_signal(channel_id))
    return max(int(row[1]) for row in rows)


async def next_rev(
    session: AsyncSession,
    channel_id: UUID,
    *,
    channel_changed: bool = False,
    message_id: UUID | None = None,
) -> int | None:
    """Take the channel's next change number for ``message_id`` -- or, with
    none, for the channel itself; ``None`` for no such channel.

    ``channel_changed``: the change is to the channel's own fields or its
    members, which subscribers are sent as ``channel.updated``.
    """
    return await _take(session, channel_id, [message_id], changed=channel_changed)


async def touch_channel(session: AsyncSession, channel_id: UUID) -> None:
    """The channel's own fields or members changed."""
    await next_rev(session, channel_id, channel_changed=True)


async def touch_channels(
    session: AsyncSession, channel_ids: Select[tuple[UUID]]
) -> None:
    """Every channel ``channel_ids`` selects changed its members: one
    statement, its signals once it commits."""
    bumped = (
        update(ChannelModel)
        .where(ChannelModel.id.in_(channel_ids.scalar_subquery()))
        .values(
            last_rev=ChannelModel.last_rev + 1,
            changed_rev=ChannelModel.last_rev + 1,
        )
        .returning(ChannelModel.id, ChannelModel.pod_id, ChannelModel.last_rev)
        .cte("bumped")
    )
    rows = await session.execute(
        insert(ChannelChangeModel)
        .add_cte(bumped)
        .from_select(
            ["channel_id", "pod_id", "rev"],
            select(bumped.c.id, bumped.c.pod_id, bumped.c.last_rev),
        )
        .returning(ChannelChangeModel.channel_id, ChannelChangeModel.pod_id)
    )
    for channel_id, pod_id in rows:
        signal_after_commit(session, pod_id, channel_signal(channel_id))


async def touch_lines(session: AsyncSession, message_ids: Collection[UUID]) -> None:
    """These lines changed in a way their readers see (their question moved,
    say): a change of its channel for each."""
    if not message_ids:
        return
    rows = await session.execute(
        select(ChannelMessageModel.id, ChannelMessageModel.channel_id).where(
            ChannelMessageModel.id.in_(sorted(message_ids))
        )
    )
    by_channel: dict[UUID, list[UUID]] = {}
    for message_id, channel_id in rows:
        by_channel.setdefault(channel_id, []).append(message_id)
    # Channels in one order, so two batches touching the same two channels
    # take their locks the same way round.
    for channel_id in sorted(by_channel):
        await _take(session, channel_id, sorted(by_channel[channel_id]))


#: ``touch_lines`` for one line, in one statement: the channel's number is
#: taken (its row locked) in the first CTE, and logged.
_TOUCH_LINE = text(
    """
    WITH bumped AS (
        UPDATE channels SET last_rev = channels.last_rev + 1
        FROM channel_messages AS line
        WHERE line.id = :message_id AND channels.id = line.channel_id
        RETURNING channels.id, channels.pod_id, channels.last_rev
    )
    INSERT INTO channel_changes (channel_id, rev, message_id, pod_id)
    SELECT bumped.id, bumped.last_rev, :message_id, bumped.pod_id FROM bumped
    RETURNING channel_changes.channel_id, channel_changes.pod_id
    """
).bindparams(bindparam("message_id", type_=PG_UUID(as_uuid=True)))


async def touch_line(session: AsyncSession, message_id: UUID) -> None:
    """One line changed in a way its readers see."""
    if session.info.get(_HELD):
        # A post holds a channel's head: count it there if it is that one.
        await touch_lines(session, [message_id])
        return
    found = (await session.execute(_TOUCH_LINE, {"message_id": message_id})).first()
    if found is not None:
        signal_after_commit(session, found[1], channel_signal(found[0]))


async def log_lines(
    session: AsyncSession, channel_id: UUID, message_ids: Select[tuple[UUID]]
) -> int:
    """Every line ``message_ids`` selects (in its order) changed: a change of
    the channel for each, in one statement. How many."""
    ids = list(await session.scalars(message_ids))
    if not ids:
        return 0
    await _take(session, channel_id, ids)
    return len(ids)


# -- the log's age ------------------------------------------------------------------

#: The oldest changes, a batch at a time, pod by pod: each pod's range of
#: ``ix_channel_changes_pod_at`` is read from its start.
_PRUNE = text(
    """
    DELETE FROM channel_changes
    WHERE (channel_id, rev) IN (
        SELECT old.channel_id, old.rev
        FROM pods
        CROSS JOIN LATERAL (
            SELECT change.channel_id, change.rev FROM channel_changes AS change
            WHERE change.pod_id = pods.id AND change.at < :before
            ORDER BY change.at
            LIMIT :limit
        ) AS old
        LIMIT :limit
    )
    """
)


async def kept_since(session: AsyncSession, channel_id: UUID, rev: int) -> bool:
    """Whether every change of the channel after ``rev`` is still in the log
    -- none pruned -- so a subscriber resuming from ``rev`` can be sent them."""
    oldest = await session.scalar(
        select(func.min(ChannelChangeModel.rev)).where(
            ChannelChangeModel.channel_id == channel_id
        )
    )
    return oldest is not None and oldest <= rev + 1


async def prune_changes(
    session: AsyncSession, *, older_than: timedelta = CHANGE_RETENTION, limit: int
) -> int:
    """Forget changes older than ``older_than``, at most ``limit``; how many.
    A subscriber resuming from before what is kept refetches (``resync``)."""
    before = await session.scalar(select(func.now() - older_than))
    result = await session.execute(_PRUNE, {"before": before, "limit": limit})
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "CHANGE_RETENTION",
    "HeldHead",
    "channel_signal",
    "hold",
    "holding",
    "kept_since",
    "log_lines",
    "next_rev",
    "prune_changes",
    "read_signal",
    "set_on_head",
    "touch_channel",
    "touch_channels",
    "touch_line",
    "touch_lines",
]
