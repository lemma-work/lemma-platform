"""Low-rate, safe aggregate telemetry for declared Redis Streams."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import registered_stream_groups
from app.core.log.log import get_logger
from app.core.config import settings
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


def observable_streams() -> set[str]:
    """Static names only: never emit tenant or dynamic Redis key names."""
    return {
        *(stream for stream, _group in registered_stream_groups()),
        *event_transport_settings.redis_stream_maxlen_overrides,
        f"streaq:{settings.worker_queue_name}:queues:normal",
    }


def _text(value: Any) -> str:
    return (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value)
    )


async def _snapshot_stream(
    client,
    stream: str,
) -> None:
    is_streaq = stream.startswith(f"streaq:{settings.worker_queue_name}:queues:")
    maxlen = (
        None if is_streaq else event_transport_settings.stream_maxlen_for(stream)
    )
    delayed = (
        int(
            await client.zcard(
                f"streaq:{settings.worker_queue_name}:queues:delayed:"
            )
        )
        if is_streaq
        else 0
    )
    if not await client.exists(stream):
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
        return

    info = await client.xinfo_stream(stream)
    group_info = await client.xinfo_groups(stream)
    stream_last_id = _value(info, "last-generated-id")
    now_ms = int(time.time() * 1000)
    stream_length = int(_value(info, "length", 0) or 0)
    stream_memory = int(await client.memory_usage(stream) or 0)
    if not group_info:
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
        return

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
        logger.info(
            "redis.stream.snapshot",
            stream=stream,
            group=_text(_value(group, "name", "unknown")),
            length=stream_length,
            delayed=delayed,
            caught_up=pending == 0 and _last_delivered_id(group) == stream_last_id,
            pending=pending,
            reported_lag=reported_lag,
            last_delivered_age_seconds=last_delivered_age_seconds,
            oldest_pending_ms=oldest_pending_ms,
            consumers=int(_value(group, "consumers", 0) or 0),
            active_consumers=active_consumers,
            memory_bytes=stream_memory,
            maxlen=maxlen or 0,
        )


async def redis_stream_snapshot_loop(message_bus) -> None:
    """Emit one bounded record per declared stream at the configured cadence."""
    interval = event_transport_settings.redis_stream_snapshot_interval_seconds
    if interval <= 0:
        return
    client = await message_bus.redis_client()
    while True:
        for stream in sorted(observable_streams()):
            incident = _snapshot_incidents.setdefault(
                stream,
                DependencyIncident(f"redis.stream.snapshot:{stream}", logger=logger),
            )
            try:
                await _snapshot_stream(client, stream)
                incident.record_success()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                incident.record_failure(error_type=type(exc).__name__)
        await asyncio.sleep(interval)
