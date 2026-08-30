"""Low-rate, safe aggregate telemetry for declared Redis Streams."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import registered_stream_groups
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident


logger = get_logger(__name__)
_snapshot_incidents: dict[str, DependencyIncident] = {}


def _value(mapping: dict[Any, Any], name: str, default: Any = 0) -> Any:
    return mapping.get(name, mapping.get(name.encode(), default))


def _last_delivered_id(group: dict[Any, Any]) -> Any:
    # Client adapters expose two names for the same Redis wire field.
    # Supporting both prevents a silent zero-age dashboard.
    return _value(
        group,
        "last-delivered-id",
        _value(group, "last-delivered-message-id", "0-0"),
    )


def _streaq_lane_queues() -> set[str]:
    """The streaq ready-queue key for every lane.

    Lanes are separate Redis queues, so watching only the interactive one would
    make a bulk-lane backlog — exactly the thing lanes exist to contain —
    invisible on dashboards.
    """
    from app.core.infrastructure.jobs.streaq_runtime import Lane, lane_queue_name

    return {f"streaq:{lane_queue_name(lane)}:queues:normal" for lane in Lane}


def observable_streams() -> set[str]:
    """Static names only: never emit tenant or dynamic Redis key names."""
    return {
        *(stream for stream, _group in registered_stream_groups()),
        *event_transport_settings.redis_stream_maxlen_overrides,
        *_streaq_lane_queues(),
    }


def _text(value: Any) -> str:
    return (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value)
    )


def _worth_reporting(
    *,
    length: int = 0,
    delayed: int = 0,
    caught_up: bool = True,
    pending: int = 0,
    reported_lag: int = 0,
    consumers: int = 0,
    active_consumers: int = 0,
) -> bool:
    """Whether this snapshot tells the reader anything.

    Every declared stream and group used to be reported on every cycle, so an
    idle deployment wrote a few dozen records saying nothing had happened —
    measured at 88% of a worker's output — and a genuine backlog had to be
    found among them. A record is now written only when some number in it is
    not the boring one; the cycle summary below is what says the loop ran.
    """
    return bool(
        not caught_up
        or pending
        or reported_lag
        or delayed
        or length
        # Consumers registered but none alive is a stalled reader — the one
        # unhealthy state an all-zero backlog would otherwise hide.
        or (consumers and not active_consumers)
    )


async def _snapshot_stream(
    client,
    stream: str,
) -> int:
    """Report this stream's groups, returning how many were worth reporting."""
    is_streaq = stream.startswith("streaq:") and ":queues:" in stream
    maxlen = None if is_streaq else event_transport_settings.stream_maxlen_for(stream)
    # Report the delayed set belonging to THIS lane's queue, not always the
    # interactive one, or a deferred bulk backlog would be attributed to the
    # wrong lane.
    delayed = (
        int(await client.zcard(f"{stream.rsplit(':queues:', 1)[0]}:queues:delayed:"))
        if is_streaq
        else 0
    )
    if not await client.exists(stream):
        # Nothing but the delayed set can be non-zero for a stream that has
        # never been written to.
        if not _worth_reporting(delayed=delayed):
            return 0
        logger.info(
            "redis.stream.snapshot",
            stream=stream,
            group="",
            length=0,
            delayed=delayed,
            caught_up=True,
            pending=0,
            reported_lag=0,
            last_delivered_age_seconds=0,
            oldest_pending_ms=0,
            consumers=0,
            active_consumers=0,
            memory_bytes=0,
            maxlen=maxlen or 0,
        )
        return 1

    info = await client.xinfo_stream(stream)
    group_info = await client.xinfo_groups(stream)
    stream_last_id = _value(info, "last-generated-id")
    now_ms = int(time.time() * 1000)
    stream_length = int(_value(info, "length", 0) or 0)
    stream_memory = int(await client.memory_usage(stream) or 0)
    if not group_info:
        # No group means nothing is reading it, so any backlog at all matters.
        if not _worth_reporting(length=stream_length, delayed=delayed):
            return 0
        logger.info(
            "redis.stream.snapshot",
            stream=stream,
            group="",
            length=stream_length,
            delayed=delayed,
            caught_up=True,
            pending=0,
            reported_lag=0,
            last_delivered_age_seconds=0,
            oldest_pending_ms=0,
            consumers=0,
            active_consumers=0,
            memory_bytes=stream_memory,
            maxlen=maxlen or 0,
        )
        return 1

    reported = 0
    for group in group_info:
        pending = int(_value(group, "pending", 0) or 0)
        raw_lag = _value(group, "lag", 0)
        reported_lag = int(raw_lag) if isinstance(raw_lag, int) else 0
        raw_id = _last_delivered_id(group)
        if isinstance(raw_id, bytes):
            raw_id = raw_id.decode("ascii", errors="ignore")
        try:
            delivered_ms = int(str(raw_id).split("-", 1)[0])
        except ValueError:
            delivered_ms = 0
        last_delivered_age_seconds = (
            max(0, (now_ms - delivered_ms) // 1000) if delivered_ms > 0 else 0
        )
        oldest_pending_ms = 0
        if pending:
            entries = await client.xpending_range(
                stream,
                _value(group, "name"),
                min="-",
                max="+",
                count=1,
            )
            if entries:
                oldest_pending_ms = int(
                    _value(entries[0], "time_since_delivered", 0) or 0
                )
        consumer_info = await client.xinfo_consumers(
            stream,
            _value(group, "name"),
        )
        active_consumer_idle_ms = (
            event_transport_settings.redis_stream_stale_consumer_seconds * 1000
        )
        active_consumers = sum(
            1
            for consumer in consumer_info
            if int(_value(consumer, "idle", active_consumer_idle_ms + 1) or 0)
            <= active_consumer_idle_ms
        )
        caught_up = pending == 0 and _last_delivered_id(group) == stream_last_id
        consumers = int(_value(group, "consumers", 0) or 0)
        if not _worth_reporting(
            # Deliberately not `length`. A stream retains its entries up to
            # maxlen, so any stream ever written to has a non-zero length for
            # the rest of its life -- and passing it here meant every healthy,
            # fully caught-up group reported on every cycle. That is what made
            # these records the bulk of a worker's log and buried everything
            # else. Whether a *reader* is behind is what `caught_up`, `pending`
            # and `reported_lag` already say. Length still counts below, in the
            # no-group branch, where nothing is reading and a pile-up is real.
            delayed=delayed,
            caught_up=caught_up,
            pending=pending,
            reported_lag=reported_lag,
            consumers=consumers,
            active_consumers=active_consumers,
        ):
            continue
        reported += 1
        logger.info(
            "redis.stream.snapshot",
            stream=stream,
            group=_text(_value(group, "name", "unknown")),
            length=stream_length,
            delayed=delayed,
            caught_up=caught_up,
            pending=pending,
            reported_lag=reported_lag,
            last_delivered_age_seconds=last_delivered_age_seconds,
            oldest_pending_ms=oldest_pending_ms,
            consumers=consumers,
            active_consumers=active_consumers,
            memory_bytes=stream_memory,
            maxlen=maxlen or 0,
        )
    return reported


async def redis_stream_snapshot_loop(message_bus) -> None:
    """Report the declared streams that need attention, at the configured cadence.

    One summary per cycle plus one record per unhealthy stream, rather than one
    record per stream per cycle. The summary is what proves the loop is alive
    and how many streams it covered, which is the only thing the all-zero
    records were really carrying.
    """
    interval = event_transport_settings.redis_stream_snapshot_interval_seconds
    if interval <= 0:
        return
    client = await message_bus.redis_client()
    while True:
        streams = sorted(observable_streams())
        reported = 0
        for stream in streams:
            incident = _snapshot_incidents.setdefault(
                stream,
                DependencyIncident(f"redis.stream.snapshot:{stream}", logger=logger),
            )
            try:
                reported += await _snapshot_stream(client, stream)
                incident.record_success()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                incident.record_failure(error_type=type(exc).__name__)
        logger.info(
            "redis.stream.snapshot_cycle",
            streams=len(streams),
            reported=reported,
        )
        await asyncio.sleep(interval)
