"""A fold's timeline: two contacts' channels become one, in the order said.

The merged channel's lines and the survivor's are numbered again together,
by when each was said -- last month's email before today's WhatsApp, not
after it -- so the survivor's timeline reads in order. Within each channel
the old order is kept (a line is placed by the latest time said so far in
its own channel, so a clock that stepped back never swaps two of one
channel's lines), and at the same instant the survivor's line comes first.

Everything hanging from a line follows it by its foreign keys: questions,
mentions (channel *and* number), threads, answers, deliveries' channel. What
is copied, rather than referenced, is moved here: how far each member had
read (``carry_read_state``), and a delivery's numbers.

All of it runs under both channels' locks (``ContactChannels.fold``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    CTE,
    ColumnElement,
    ScalarSelect,
    and_,
    delete,
    func,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.infrastructure.models import (
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelPushPendingModel,
    ChannelReadStateModel,
)

#: Checked at the fold's commit, not per statement: a channel's places and
#: its lines move in different statements.
_DEFER_PLACES = text(
    "SET CONSTRAINTS fk_channel_messages_source, "
    "fk_channel_messages_target_source DEFERRED"
)


def _ordered(merged_id: UUID, survivor_id: UUID, name: str) -> CTE:
    """Every line of both channels: its channel and number now, and its
    number once merged (``new_seq``)."""
    line = ChannelMessageModel
    said = (
        func.max(line.created_at)
        .over(partition_by=line.channel_id, order_by=line.seq)
        .label("said")
    )
    lines = (
        select(
            line.id.label("id"),
            line.channel_id.label("origin"),
            line.seq.label("old_seq"),
            said,
        )
        .where(line.channel_id.in_([merged_id, survivor_id]))
        .subquery(f"{name}_lines")
    )
    return select(
        lines.c.id,
        lines.c.origin,
        lines.c.old_seq,
        func.row_number()
        .over(
            order_by=(
                lines.c.said,
                lines.c.origin != survivor_id,
                lines.c.old_seq,
            )
        )
        .label("new_seq"),
    ).cte(name)


def _renumbered(ordered: CTE, survivor_id: UUID) -> ColumnElement[bool]:
    """A line that moves channel or takes another number."""
    return ~and_(
        ordered.c.origin == survivor_id, ordered.c.old_seq == ordered.c.new_seq
    )


async def defer_place_checks(session: AsyncSession) -> None:
    await session.execute(_DEFER_PLACES)


async def carry_read_state(
    session: AsyncSession, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
) -> None:
    """Each member who had read either channel has read the merged one up to
    just before the first line they had not read in either -- never past a
    line they had not seen, and not back to the start for the lines they
    had. Read before the lines are renumbered."""
    state = ChannelReadStateModel
    ordered = _ordered(merged_id, survivor_id, "ordered")
    readers = (
        select(state.user_id)
        .where(state.channel_id.in_([merged_id, survivor_id]))
        .distinct()
        .subquery("readers")
    )

    def first_unread(channel_id: UUID) -> ScalarSelect[int]:
        read = (
            select(state.last_read_seq)
            .where(state.user_id == readers.c.user_id, state.channel_id == channel_id)
            .scalar_subquery()
        )
        return (
            select(ordered.c.new_seq)
            .where(
                ordered.c.origin == channel_id,
                ordered.c.old_seq == func.coalesce(read, 0) + 1,
            )
            .scalar_subquery()
        )

    total = select(func.count()).select_from(ordered).scalar_subquery()
    upto = func.coalesce(
        func.least(first_unread(survivor_id), first_unread(merged_id)) - 1, total
    )
    statement = insert(state).from_select(
        ["user_id", "channel_id", "pod_id", "last_read_seq"],
        select(readers.c.user_id, literal(survivor_id), literal(pod_id), upto),
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[state.user_id, state.channel_id],
            set_={
                "last_read_seq": statement.excluded.last_read_seq,
                "updated_at": func.now(),
            },
        )
    )


async def recount_read_replies(session: AsyncSession, *, channel_id: UUID) -> None:
    """How many replies each reader of the folded channel has read: those up
    to their read number, as the lines are numbered now."""
    state, line = ChannelReadStateModel, ChannelMessageModel
    await session.execute(
        update(state)
        .where(state.channel_id == channel_id)
        .values(
            read_replies=select(func.count())
            .where(
                line.channel_id == channel_id,
                line.seq <= state.last_read_seq,
                line.thread_root_id.is_not(None),
            )
            .scalar_subquery()
        )
        .execution_options(synchronize_session=False)
    )


async def renumber_deliveries(
    session: AsyncSession, *, merged_id: UUID, survivor_id: UUID
) -> None:
    """A delivery's line numbers, as they will be: read before the lines are
    renumbered (its channel follows its line by the foreign key). A push
    window's start is the line it started at, renumbered."""
    d = ChannelMessageDeliveryModel
    ordered = _ordered(merged_id, survivor_id, "ordered")
    start = ordered.alias("window_start")
    from_seq = (
        select(start.c.new_seq)
        .where(start.c.origin == d.channel_id, start.c.old_seq == d.from_seq)
        .scalar_subquery()
    )
    await session.execute(
        update(d)
        .where(d.message_id == ordered.c.id, _renumbered(ordered, survivor_id))
        .values(seq=ordered.c.new_seq, from_seq=from_seq)
        .execution_options(synchronize_session=False)
    )
    # A push waiting to go names a range of numbers that no longer mean
    # those lines: dropped; the lines still unread are in the badge, and the
    # next line heard starts a new one.
    pending = ChannelPushPendingModel
    await session.execute(
        delete(pending).where(pending.channel_id.in_([merged_id, survivor_id]))
    )


async def merge_lines(
    session: AsyncSession, *, merged_id: UUID, survivor_id: UUID
) -> list[UUID]:
    """Number both channels' lines together, in the survivor; the lines that
    moved or took another number.

    Two steps, because ``(channel_id, seq)`` is checked row by row: every
    line that changes takes its new number negated, then the sign goes. A
    line that keeps its channel and number is not written at all.
    """
    line = ChannelMessageModel
    ordered = _ordered(merged_id, survivor_id, "ordered")
    changed = list(
        await session.scalars(
            update(line)
            .where(
                line.id == ordered.c.id,
                _renumbered(ordered, survivor_id),
            )
            .values(channel_id=survivor_id, seq=-ordered.c.new_seq)
            .returning(line.id)
            .execution_options(synchronize_session=False)
        )
    )
    if changed:
        await session.execute(
            update(line)
            .where(line.channel_id == survivor_id, line.seq < 0)
            .values(seq=-line.seq)
            .execution_options(synchronize_session=False)
        )
    return changed


__all__ = [
    "carry_read_state",
    "recount_read_replies",
    "defer_place_checks",
    "merge_lines",
    "renumber_deliveries",
]
