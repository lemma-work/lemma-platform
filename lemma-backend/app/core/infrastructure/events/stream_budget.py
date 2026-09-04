"""Trim streams against a byte budget, not an entry count.

`REDIS_STREAM_MAXLEN` bounds entries, and entries are not the thing that runs
out. The same 50,000-entry cap meant 9MB for one stream and 831MB for another,
because their payloads differ by more than an order of magnitude -- and when the
payload grew, the count cap could not react at all: the bytes moved 5.5x while
the length stayed pinned at 50,000. That is the shape of the outage this exists
to prevent.

The count cap stays: it is enforced on every publish and costs nothing. This is
the backstop that notices when the count was the wrong unit.

**It cannot lose unread work.** Trimming is done with `MINID` at the oldest id
every consumer group has already delivered, so only fully-consumed entries are
removed. When that is not enough to meet the budget, the stream is reported and
left alone -- a budget is not a reason to destroy work someone still needs.
"""

from __future__ import annotations

from redis.exceptions import RedisError

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_observability import observable_streams
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _as_id(value: object) -> tuple[int, int] | None:
    """A stream id as a comparable pair, or ``None`` if it is not one."""
    text = value.decode() if isinstance(value, bytes) else value
    if not isinstance(text, str) or "-" not in text:
        return None
    left, _, right = text.partition("-")
    try:
        return int(left), int(right)
    except ValueError:
        return None


def _safe_minid(groups: object) -> str | None:
    """The oldest id every group has delivered, below which nothing is unread.

    ``None`` when any group's progress is unreadable: an unknown consumer is
    treated as one that has read nothing, which is the cautious answer.
    """
    if not isinstance(groups, list) or not groups:
        return None
    oldest: tuple[int, int] | None = None
    for group in groups:
        if not isinstance(group, dict):
            return None
        raw = group.get("last-delivered-id") or group.get(b"last-delivered-id")
        parsed = _as_id(raw)
        if parsed is None:
            return None
        oldest = parsed if oldest is None else min(oldest, parsed)
    if oldest is None:
        return None
    return f"{oldest[0]}-{oldest[1]}"


async def trim_streams_to_budget(
    client, *, streams: set[str] | None = None
) -> dict[str, int]:
    """Trim every over-budget stream down to its consumed watermark.

    Returns the bytes reclaimed per stream, for the caller to report.

    ``streams`` defaults to every observable stream; it is a parameter so a
    caller (and a test) can name the set rather than reach into this module.
    """
    budget = event_transport_settings.redis_stream_max_bytes
    if budget <= 0:
        return {}

    reclaimed: dict[str, int] = {}
    for stream in sorted(observable_streams() if streams is None else streams):
        try:
            before = int(await client.memory_usage(stream) or 0)
        except RedisError, TypeError, ValueError:
            logger.warning(
                "redis.stream.over_budget.degraded",
                stream=stream,
                memory_bytes=0,
                budget_bytes=budget,
                reason="memory_unreadable",
                exc_info=True,
            )
            continue
        if before <= budget:
            continue

        try:
            groups = await client.xinfo_groups(stream)
        except RedisError:
            # Never fall through to "no groups": that path trims the whole
            # stream, and a transient error is not evidence that nothing is
            # reading it.
            logger.warning(
                "redis.stream.over_budget.degraded",
                stream=stream,
                memory_bytes=before,
                budget_bytes=budget,
                reason="consumer_progress_unreadable",
                exc_info=True,
            )
            continue
        minid = _safe_minid(groups)
        if minid is None:
            # No groups at all means nothing is reading it, so the whole stream
            # is consumed by definition; unreadable groups mean the opposite.
            if groups:
                logger.warning(
                    "redis.stream.over_budget.degraded",
                    stream=stream,
                    memory_bytes=before,
                    budget_bytes=budget,
                    reason="consumer_progress_unreadable",
                )
                continue
            minid = "+"

        try:
            await client.xtrim(stream, minid=minid, approximate=True)
            after = int(await client.memory_usage(stream) or 0)
        except RedisError, TypeError, ValueError:
            logger.warning(
                "redis.stream.over_budget.degraded",
                stream=stream,
                memory_bytes=before,
                budget_bytes=budget,
                reason="trim_failed",
                exc_info=True,
            )
            continue

        reclaimed[stream] = max(0, before - after)
        if after > budget:
            # Everything removable was removed and it is still too big: the
            # entries that remain are unread. Say so rather than trimming them.
            logger.warning(
                "redis.stream.over_budget.degraded",
                stream=stream,
                memory_bytes=after,
                budget_bytes=budget,
                reason="unread_entries_exceed_budget",
            )
    return reclaimed
