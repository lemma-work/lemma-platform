"""Give a consumer group back what the memory budget trimmed from under it.

``stream_budget`` will trim unread entries rather than let Redis run out of
memory. That is only acceptable because the entries are not the source of
truth: every event is written to the PostgreSQL outbox first and kept there for
``EVENT_COMPLETED_RETENTION_DAYS`` after it is published. So a trim records a
*gap* -- the stream, the group, and the window it lost -- and this module
re-publishes that window from the outbox once the group is reading again.

Re-publishing puts the events back on the stream for every group, not only the
one that lost them. That is safe because every stream consumer runs through the
inbox (``InboxConsumer.process``), which is unique on ``(consumer, event_id)``:
a group that already handled an event finds it settled and acknowledges it
without running anything.

Replay is paced by the group it is for. A batch is only started once the group
has less than a batch of backlog, so the stream is refilled no faster than it is
read -- replaying a large gap in one go would put it straight back over the
budget that caused it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.stream_budget import (
    GroupState,
    StreamGap,
    int_field,
    text,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

GAP_KEY_PREFIX = "lemma:stream-gap:"

#: Outbox rows are marked published a moment after their XADD, and in batches,
#: so ``published_at`` trails the stream id's clock. Widening the window by this
#: much on each side costs a few duplicate deliveries the inbox absorbs, and
#: never misses one.
_CLOCK_SKEW_MS = 5 * 60 * 1000

# Merge rather than overwrite: two trims of the same group before its replay
# finishes widen one window instead of the second forgetting the first.
_RECORD_GAP_LUA = """
local after = redis.call('HGET', KEYS[1], 'after_ms')
local untl = redis.call('HGET', KEYS[1], 'until_ms')
if (not after) or tonumber(ARGV[1]) < tonumber(after) then
  redis.call('HSET', KEYS[1], 'after_ms', ARGV[1])
end
if (not untl) or tonumber(ARGV[2]) > tonumber(untl) then
  redis.call('HSET', KEYS[1], 'until_ms', ARGV[2])
end
local changed = 0
if (not after) or tonumber(ARGV[1]) < tonumber(after) then changed = 1 end
if (not untl) or tonumber(ARGV[2]) > tonumber(untl) then changed = 1 end
if changed == 1 then
  redis.call('HSET', KEYS[1], 'recorded_ms', ARGV[3])
end
redis.call('EXPIRE', KEYS[1], ARGV[4])
return changed
"""


def gap_key(stream: str, group: str) -> str:
    return f"{GAP_KEY_PREFIX}{stream}:{group}"


def _retention_seconds() -> int:
    return event_transport_settings.event_completed_retention_days * 24 * 60 * 60


async def record_gap(client, gap: StreamGap, *, now_ms: int | None = None) -> None:
    """Persist ``gap`` so it is replayed; never raises.

    Reported when the gap is new or wider than what was already recorded, so a
    loss that is observed again on every guard pass is one line, not one per
    pass. If Redis is too full to take even this small write, the error line
    carries the whole window, and that is what an operator replays from
    (``python -m app.core.infrastructure.events.admin replay-window``).
    """
    recorded_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        changed = await client.eval(
            _RECORD_GAP_LUA,
            1,
            gap_key(gap.stream, gap.group),
            gap.after_ms,
            gap.until_ms,
            recorded_ms,
            _retention_seconds(),
        )
    except RedisError, OSError:
        logger.error(
            "redis.stream.gap_record.failed",
            stream_name=gap.stream,
            group=gap.group,
            after_ms=gap.after_ms,
            until_ms=gap.until_ms,
            exc_info=True,
        )
        return
    if int(changed or 0):
        logger.error(
            "redis.stream.unread_trimmed",
            stream_name=gap.stream,
            group=gap.group,
            after_ms=gap.after_ms,
            until_ms=gap.until_ms,
        )


@dataclass(frozen=True, slots=True)
class RecordedGap:
    stream: str
    group: str
    after_ms: int
    until_ms: int
    recorded_ms: int


async def recorded_gaps(client) -> list[RecordedGap]:
    gaps: list[RecordedGap] = []
    async for raw_key in client.scan_iter(match=f"{GAP_KEY_PREFIX}*", count=100):
        key = text(raw_key)
        fields = await client.hgetall(key)
        suffix = key[len(GAP_KEY_PREFIX) :]
        # Group names never contain ':'; stream names may (``:dead``), so the
        # split is from the right.
        stream, _, group = suffix.rpartition(":")
        after_ms = int_field(fields, "after_ms", default=-1)
        until_ms = int_field(fields, "until_ms", default=-1)
        recorded_ms = int_field(fields, "recorded_ms", default=-1)
        if min(after_ms, until_ms, recorded_ms) < 0:
            logger.warning("redis.stream.gap_unreadable.degraded", gap_key=key)
            continue
        gaps.append(
            RecordedGap(
                stream=stream,
                group=group,
                after_ms=after_ms,
                until_ms=until_ms,
                recorded_ms=recorded_ms,
            )
        )
    return gaps


def replay_window(
    after_ms: int, until_ms: int, recorded_ms: int
) -> tuple[datetime, datetime]:
    """The ``published_at`` range whose outbox rows cover a trimmed window.

    Capped at the moment the gap was recorded: a row re-published by the replay
    itself is stamped later than that, so it can never fall back into the window
    and be replayed again.
    """
    lower = max(0, after_ms - _CLOCK_SKEW_MS)
    upper = min(until_ms + _CLOCK_SKEW_MS, recorded_ms)
    return (
        datetime.fromtimestamp(lower / 1000, tz=timezone.utc),
        datetime.fromtimestamp(upper / 1000, tz=timezone.utc),
    )


async def requeue_outbox_window(
    session_maker: Callable[[], AsyncSession],
    *,
    stream: str,
    lower: datetime,
    upper: datetime,
    limit: int,
) -> int:
    """Mark up to ``limit`` published rows in the window for re-publication.

    The dispatcher then publishes them as it does any unpublished row. Each
    re-queued row leaves the window (its ``published_at`` is cleared, and set
    past ``upper`` once re-published), so repeated calls walk the window
    without a cursor.
    """
    async with session_maker() as session, session.begin():
        ids = (
            await session.scalars(
                select(DomainEventOutbox.id)
                .where(
                    DomainEventOutbox.published_at.is_not(None),
                    DomainEventOutbox.published_at >= lower,
                    DomainEventOutbox.published_at <= upper,
                    DomainEventOutbox.stream == stream,
                )
                .order_by(DomainEventOutbox.published_at, DomainEventOutbox.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not ids:
            return 0
        await session.execute(
            update(DomainEventOutbox)
            .where(DomainEventOutbox.id.in_(ids))
            .values(
                published_at=None,
                available_at=datetime.now(timezone.utc),
                attempts=0,
                lease_owner=None,
                lease_until=None,
            )
        )
        return len(ids)


def _group_can_take_more(group: GroupState | None, batch_size: int) -> bool:
    """Whether the group is reading and has room for another batch."""
    if group is None:
        return False
    stall_ms = event_transport_settings.redis_stream_stall_seconds * 1000
    if group.reader_inactive_ms is None or (
        stall_ms and group.reader_inactive_ms > stall_ms
    ):
        return False
    return group.lag is not None and group.lag < batch_size


async def replay_gaps(
    client,
    session_maker: Callable[[], AsyncSession],
    groups: Mapping[tuple[str, str], GroupState],
    *,
    now_ms: int | None = None,
) -> int:
    """One replay step for every recorded gap whose group can take it.

    Returns how many outbox rows were re-queued.
    """
    batch_size = event_transport_settings.redis_stream_gap_replay_batch_size
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    retention_ms = _retention_seconds() * 1000
    requeued = 0
    for gap in await recorded_gaps(client):
        key = gap_key(gap.stream, gap.group)
        if gap.after_ms < now - retention_ms:
            # Part of the window is older than anything the outbox still has.
            # Replay what remains, but say plainly that the rest is gone.
            logger.error(
                "redis.stream.gap_unrecoverable",
                stream_name=gap.stream,
                group=gap.group,
                after_ms=gap.after_ms,
                until_ms=gap.until_ms,
            )
        if not _group_can_take_more(groups.get((gap.stream, gap.group)), batch_size):
            continue
        lower, upper = replay_window(gap.after_ms, gap.until_ms, gap.recorded_ms)
        count = await requeue_outbox_window(
            session_maker,
            stream=gap.stream,
            lower=lower,
            upper=upper,
            limit=batch_size,
        )
        requeued += count
        if count < batch_size:
            await client.delete(key)
            logger.info(
                "redis.stream.gap_replayed",
                stream_name=gap.stream,
                group=gap.group,
                after_ms=gap.after_ms,
                until_ms=gap.until_ms,
            )
    return requeued
