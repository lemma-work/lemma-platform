"""A fold's timeline: the merged contact's lines go after the survivor's.

The survivor's lines keep their numbers. The merged channel's follow them, in
their own order, numbered on from the survivor's last (``base``). Nothing the
survivor's readers held moves: their positions, their pushes, their
deliveries' numbers still mean the same lines.

Everything hanging from a moved line follows it by its foreign keys:
questions, mentions (channel *and* number), threads, answers, deliveries'
channel. What is copied, rather than referenced, is moved here: how far each
member had read (``carry_read_state``), and a delivery's numbers.

All of it runs under both channels' locks (``ContactChannels.fold``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CTE, case, delete, func, literal, select, text, union, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
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


def _appended(merged_id: UUID, base: int) -> CTE:
    """Every line of the merged channel: its number now, and its number once
    appended after the survivor's ``base``."""
    line = ChannelMessageModel
    return (
        select(
            line.id.label("id"),
            line.seq.label("old_seq"),
            (literal(base) + func.row_number().over(order_by=line.seq)).label(
                "new_seq"
            ),
        )
        .where(line.channel_id == merged_id)
        .cte("appended")
    )


async def defer_place_checks(session: AsyncSession) -> None:
    await session.execute(_DEFER_PLACES)


async def carry_read_state(
    session: AsyncSession,
    *,
    pod_id: UUID,
    merged_id: UUID,
    survivor_id: UUID,
    base: int,
    moved: int,
) -> None:
    """Where each member has read the folded channel up to, so that moving
    makes nothing read unread, and nothing unread read.

    A member's place in each channel is how far they had read it -- or, with
    no read row, nothing if they were seated there (its lines counted as
    unread for them) and everything if they were not (its lines were never
    news to them). One mark holds both channels now: a member with unread
    lines of the survivor's stays where they were, the moved lines after it;
    one who had read the survivor's to the end reads on into the moved lines
    as far as they had read the merged channel. Read before the seats and
    the lines move."""
    state, seat = ChannelReadStateModel, ChannelMemberModel
    channels = [merged_id, survivor_id]
    people = union(
        select(state.user_id).where(state.channel_id.in_(channels)),
        select(seat.user_id).where(
            seat.channel_id.in_(channels), seat.user_id.is_not(None)
        ),
    ).subquery("people")

    def place_in(channel_id: UUID, everything: int):
        read = (
            select(state.last_read_seq)
            .where(state.user_id == people.c.user_id, state.channel_id == channel_id)
            .scalar_subquery()
        )
        seated = (
            select(seat.id)
            .where(seat.channel_id == channel_id, seat.user_id == people.c.user_id)
            .exists()
        )
        return func.coalesce(read, case((seated, 0), else_=everything))

    kept = place_in(survivor_id, base)
    upto = case(
        (kept < base, kept),
        else_=base + func.least(place_in(merged_id, moved), moved),
    )
    statement = insert(state).from_select(
        ["user_id", "channel_id", "pod_id", "last_read_seq"],
        select(people.c.user_id, literal(survivor_id), literal(pod_id), upto),
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


async def shift_deliveries(
    session: AsyncSession, *, merged_id: UUID, base: int
) -> None:
    """A moved line's delivery numbers, as they will be: read before the
    lines move (its channel follows its line by the foreign key). A push
    window's start is the line it started at, moved."""
    d = ChannelMessageDeliveryModel
    appended = _appended(merged_id, base)
    start = appended.alias("window_start")
    from_seq = (
        select(start.c.new_seq).where(start.c.old_seq == d.from_seq).scalar_subquery()
    )
    await session.execute(
        update(d)
        .where(d.message_id == appended.c.id)
        .values(seq=appended.c.new_seq, from_seq=from_seq)
        .execution_options(synchronize_session=False)
    )
    # A push waiting to go in the merged channel names numbers that no
    # longer mean those lines: dropped; the lines still unread are in the
    # badge, and the next line heard starts a new one. The survivor's still
    # mean what they did.
    pending = ChannelPushPendingModel
    await session.execute(delete(pending).where(pending.channel_id == merged_id))


async def append_lines(
    session: AsyncSession, *, merged_id: UUID, survivor_id: UUID, base: int
) -> list[UUID]:
    """The merged channel's lines, after the survivor's ``base``, in their
    own order; the lines moved. One statement: every moved line takes a
    number above any the survivor has, so no two ever meet."""
    line = ChannelMessageModel
    appended = _appended(merged_id, base)
    return list(
        await session.scalars(
            update(line)
            .where(line.id == appended.c.id)
            .values(channel_id=survivor_id, seq=appended.c.new_seq)
            .returning(line.id)
            .execution_options(synchronize_session=False)
        )
    )


__all__ = [
    "append_lines",
    "carry_read_state",
    "defer_place_checks",
    "recount_read_replies",
    "shift_deliveries",
]
