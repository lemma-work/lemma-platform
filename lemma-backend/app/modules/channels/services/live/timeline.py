"""What a channel's subscribers are to be sent, worked out before any is sent.

The hub (``hub.py``) reads, then offers. Everything that reads the database
for a batch is here and returns a plan; the hub offers the plan with nothing
awaited in between and only then moves its cursor, so a read that fails part
way leaves nothing half sent and nothing skipped.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.protocol import ResyncReason
from app.modules.channels.api.access import Caller
from app.modules.channels.domain.access import may_see
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import ChannelReadStateModel
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.revisions import kept_since
from app.modules.channels.services.live.events import (
    Head,
    channel_updated,
    heads_of,
    lines_view,
    parse_cursor,
    read_updated,
)
from app.modules.channels.services.live.readers import caller_for
from app.modules.channels.services.live.subscriptions import ChannelSub

#: How far back a poll looks for read positions, past the last one: a read
#: committed just after a poll began is still found by the next.
_READ_OVERLAP = timedelta(seconds=5)


@dataclass(slots=True)
class Plan:
    """One channel's batch, per subscriber."""

    #: Those who may no longer see the channel.
    revoked: list[ChannelSub] = field(default_factory=list)
    #: Each other subscriber's events; ``None``: too many to send, resync.
    sends: list[tuple[ChannelSub, list[str] | None]] = field(default_factory=list)


async def plan_changes(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    subs: Collection[ChannelSub],
    *,
    since: Head,
    head: Head,
    limit: int,
) -> Plan:
    """The channel's changes after ``since`` up to ``head``, for each
    subscriber as they may read them."""
    # Up to the head read for this batch, whose cursor it is sent under: a
    # line changed since is the next batch's, not sent twice nor ahead of it.
    lines = await LineRepository(uow.session).changed_since(
        channel.id, after_rev=since.rev, limit=limit + 1, up_to_rev=head.rev
    )
    view = (
        await lines_view(uow, channel, lines, since_seq=since.seq)
        if len(lines) <= limit
        else None
    )
    members = await MemberRepository(uow.session).users_in(
        channel.id, {sub.user_id for sub in subs}
    )
    plan = Plan()
    for sub in subs:
        if not may_see(channel, sub.caller.viewer, is_member=sub.user_id in members):
            plan.revoked.append(sub)
        elif view is None:
            plan.sends.append((sub, None))
        else:
            events: list[str] = []
            if head.changed_rev > since.rev:
                events.append(await channel_updated(uow, sub.caller, channel))
            events.extend(view.events_for(sub.user_id))
            plan.sends.append((sub, events))
    return plan


async def gap_since(
    uow: SqlAlchemyUnitOfWork,
    sub: ChannelSub,
    channel: Channel,
    head: Head,
    since: str,
    *,
    limit: int,
) -> list[str] | ResyncReason:
    """What a subscriber resuming from ``since`` missed up to ``head``, or why
    it must refetch instead."""
    position = parse_cursor(since)
    if position is None:
        return ResyncReason.CURSOR_EXPIRED
    since_rev, since_seq = position
    if since_rev > head.rev:
        # Past this hub's head: another process's hub may simply be ahead of
        # it. Past the channel's own, it is no cursor this topic gave out.
        now = (await heads_of(uow, [channel.id])).get(channel.id)
        if now is None or since_rev > now.rev or since_seq > now.seq:
            return ResyncReason.CURSOR_EXPIRED
        return []
    if since_seq > head.seq:
        # A line numbered past the channel's newest, at a change no later than
        # its own: no cursor this topic gave out. Read as one, every line up
        # to it would be told as edited rather than created.
        return ResyncReason.CURSOR_EXPIRED
    if since_rev == head.rev:
        return []
    if not await kept_since(uow.session, channel.id, since_rev):
        # Older than the change log keeps (``revisions.CHANGE_RETENTION``).
        return ResyncReason.CURSOR_EXPIRED
    lines = await LineRepository(uow.session).changed_since(
        channel.id, after_rev=since_rev, limit=limit + 1, up_to_rev=head.rev
    )
    if len(lines) > limit:
        return ResyncReason.TOO_MANY
    events: list[str] = []
    if head.changed_rev > since_rev:
        events.append(await channel_updated(uow, sub.caller, channel))
    view = await lines_view(uow, channel, lines, since_seq=since_seq)
    events.extend(view.events_for(sub.user_id))
    return events


async def readers_now(
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    watched: Mapping[UUID, Collection[ChannelSub]],
) -> tuple[list[ChannelSub], list[tuple[ChannelSub, Caller]]]:
    """Every subscriber rebuilt as the channel rules see them now: those who
    may no longer see their channel, and the rest with who they now are."""
    callers: dict[UUID, Caller | None] = {}
    channels = await ChannelRepository(uow.session).by_ids(set(watched))
    revoked: list[ChannelSub] = []
    kept: list[tuple[ChannelSub, Caller]] = []
    for channel_id, subs in watched.items():
        channel = channels.get(channel_id)
        members = await MemberRepository(uow.session).users_in(
            channel_id, {sub.user_id for sub in subs}
        )
        for sub in subs:
            if sub.user_id not in callers:
                callers[sub.user_id] = await caller_for(
                    uow, pod_id=pod_id, user_id=sub.user_id
                )
            caller = callers[sub.user_id]
            if (
                caller is None
                or channel is None
                or not may_see(channel, caller.viewer, is_member=sub.user_id in members)
            ):
                revoked.append(sub)
            else:
                kept.append((sub, caller))
    return revoked, kept


async def read_updates(
    uow: SqlAlchemyUnitOfWork,
    reads: Collection[tuple[UUID, UUID]],
    watched: Mapping[UUID, Collection[ChannelSub]],
) -> list[tuple[ChannelSub, str]]:
    """``read.updated`` for each of the reader's own subscriptions of a
    channel whose read position moved."""
    members = MemberRepository(uow.session)
    updates: list[tuple[ChannelSub, str]] = []
    for channel_id, user_id in sorted(reads):
        subs = [
            sub
            for sub in watched.get(channel_id, ())
            if sub.active and sub.user_id == user_id
        ]
        if subs:
            seq = await members.read_seq(channel_id=channel_id, user_id=user_id)
            event = read_updated(channel_id, seq)
            updates.extend((sub, event) for sub in subs)
    return updates


async def reads_moved(
    uow: SqlAlchemyUnitOfWork, users: Collection[UUID], since: datetime
) -> set[tuple[UUID, UUID]]:
    """The (channel, reader) read positions of ``users`` moved since ``since``."""
    if not users:
        return set()
    state = ChannelReadStateModel
    rows = await uow.session.execute(
        select(state.channel_id, state.user_id).where(
            state.user_id.in_(sorted(users)),
            state.updated_at > since - _READ_OVERLAP,
        )
    )
    return {(row[0], row[1]) for row in rows}


__all__ = [
    "Plan",
    "gap_since",
    "plan_changes",
    "read_updates",
    "reads_moved",
    "readers_now",
]
