"""Shared FastStream Redis message bus resource."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any
from pydantic import BaseModel
from faststream.redis import RedisBroker
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import (
    ensure_stream_groups,
    registered_groups_for_stream,
)
from app.core.log.log import get_dependency_logger, get_logger

logger = get_logger(__name__)


#: One consumer-group info mapping as the Redis client hands it back. Keys are
#: `str` or `bytes` depending on the adapter and values are wire scalars, which
#: is why `_redis_value` exists to read it.
RedisGroupInfo = dict[Any, Any]


def _redis_value(mapping: dict[Any, Any], name: str, default: Any = None) -> Any:
    return mapping.get(name, mapping.get(name.encode(), default))


def _last_delivered_id(group: dict[Any, Any]) -> Any:
    # Redis client adapters normalize this wire field under two common names.
    return _redis_value(
        group,
        "last-delivered-id",
        _redis_value(group, "last-delivered-message-id"),
    )


def _observed_groups(groups: object) -> dict[str, RedisGroupInfo]:
    """Consumer groups keyed by name, skipping anything not shaped like one."""
    observed: dict[str, RedisGroupInfo] = {}
    if not isinstance(groups, list):
        return observed
    for group in groups:
        if not isinstance(group, dict):
            continue
        raw_name = group.get("name", "")
        name = (
            raw_name.decode("utf-8", errors="replace")
            if isinstance(raw_name, bytes)
            else str(raw_name)
        )
        observed[name] = group
    return observed


async def _pending_is_stale(redis_client, stream: str, group_name: str) -> bool:
    """Whether a group's oldest unacked entry has been idle past the hold window.

    A message that has sat unacked for the whole reclaim window has already
    failed both its consumer and the reclaimer. Treating it as live work means
    one entry keeps every entry behind it, forever and silently.

    Unreadable pending state answers "not stale", which keeps the cautious
    behaviour: a stream whose state we cannot see is one we do not trim through.
    """
    hold = event_transport_settings.redis_stream_pending_hold_seconds
    if hold <= 0:
        return False
    try:
        entries = await redis_client.xpending_range(
            stream, group_name, min="-", max="+", count=1
        )
    except RedisError:
        # Narrow on purpose: the answer below is the cautious one, and this runs
        # on every publish, so reporting a Redis blip here would be a log line
        # per message rather than a signal.
        return False
    if not isinstance(entries, list) or not entries:
        return False
    first = entries[0]
    idle = (
        _redis_value(first, "time_since_delivered") if isinstance(first, dict) else None
    )
    return isinstance(idle, int) and idle >= hold * 1000


async def _group_holding_back_trim(
    redis_client,
    stream: str,
    observed: dict[str, RedisGroupInfo],
    stream_last_id: object,
    maxlen: int,
) -> tuple[str, str] | None:
    """The first group whose unread work forbids the normal cap, and why.

    Extra groups may be obsolete, but they still own pending/unread work. Never
    trim through them implicitly; cleanup is an explicit operation -- except for
    an entry so old it is no longer work anyone is doing, which is the case that
    turned a bounded stream into an unbounded one in production.
    """
    for group_name, group in observed.items():
        pending = _redis_value(group, "pending")
        lag = _redis_value(group, "lag")
        caught_up = pending == 0 and _last_delivered_id(group) == stream_last_id
        if pending != 0 and not await _pending_is_stale(
            redis_client, stream, group_name
        ):
            return group_name, "pending"
        if not caught_up and (not isinstance(lag, int) or lag > maxlen // 2):
            return group_name, "lagging"
    return None


class FastStreamRedisMessageBus:
    """Message bus implementation backed by FastStream Redis broker."""

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._broker: RedisBroker | None = None
        self._lock = asyncio.Lock()

    async def _get_broker(self) -> RedisBroker:
        if self._broker:
            return self._broker

        async with self._lock:
            if not self._broker:
                broker = RedisBroker(
                    self._redis_url,
                    logger=get_dependency_logger("faststream.redis"),
                    log_level=logging.INFO,
                )
                try:
                    await broker.connect()
                except asyncio.CancelledError:
                    try:
                        await broker.stop()
                    except Exception:
                        # Warning, not error: the cancellation is re-raised
                        # below and is the loud event. What was invisible is a
                        # half-open Redis connection that `stop()` could not
                        # close.
                        logger.warning(
                            "infrastructure.message_bus.cancelled_broker_stop.degraded",
                            exc_info=True,
                        )
                    raise
                except Exception:
                    try:
                        await broker.stop()
                    except Exception:
                        logger.warning(
                            "infrastructure.message_bus.partial_broker_stop.degraded",
                            exc_info=True,
                        )
                    raise
                self._broker = broker
        return self._broker

    async def connect(self) -> RedisBroker:
        """Eagerly initialize the shared broker connection."""
        return await self._get_broker()

    async def redis_client(self):
        """Return the connected raw client for aggregate health inspection."""
        broker = await self._get_broker()
        client = getattr(broker, "_connection", None)
        if client is None:
            raise ConnectionError("Redis message bus has no active connection")
        return client

    @staticmethod
    def _relaxed_maxlen(
        stream: str, *, reason: str, group: str | None = None
    ) -> int | None:
        """The ceiling to publish at when consumer progress forbids the normal cap.

        Reported every time rather than once: the condition is per-publish, and a
        stream sitting here is one whose retention has silently changed.
        """
        hard = event_transport_settings.stream_hard_maxlen_for(stream)
        logger.warning(
            "redis.stream.trim_degraded.degraded",
            stream=stream,
            reason=reason,
            group=group,
            maxlen=event_transport_settings.stream_maxlen_for(stream),
            hard_maxlen=hard,
        )
        return hard

    async def _safe_publish_maxlen(self, redis_client, stream: str) -> int | None:
        """Choose MAXLEN, relaxing -- never removing -- the cap for unread work.

        FastStream's MAXLEN option does not account for consumer progress.
        Ungrouped streams are explicitly ephemeral and may be capped directly.
        Grouped streams require zero pending deliveries and retain at least
        twice the largest reported lag.

        When those conditions do not hold the stream is published at its *hard*
        ceiling rather than uncapped. This used to return ``None`` -- XADD with
        no MAXLEN at all -- on the reasoning that trimming through unread work
        is worse than keeping it. It is, but the failure mode it chose is
        unbounded growth: one consumer group with a single unacked message
        switched trimming off for the whole stream, which then grew until Redis
        was OOM-killed and login went down. Protecting a backlog has to be
        bounded too, so a stuck consumer degrades to retaining more rather than
        retaining everything, and says so.
        """
        maxlen = event_transport_settings.stream_maxlen_for(stream)
        if maxlen is None:
            return None
        declared_groups = registered_groups_for_stream(stream)
        try:
            groups = await redis_client.xinfo_groups(stream)
        except Exception:
            # A not-yet-created stream has no group state to protect.
            if not await redis_client.exists(stream):
                return maxlen
            return self._relaxed_maxlen(stream, reason="group_state_unreadable")
        observed = _observed_groups(groups)
        if not declared_groups <= observed.keys():
            return self._relaxed_maxlen(stream, reason="declared_group_missing")
        if not observed:
            return maxlen
        stream_info = await redis_client.xinfo_stream(stream)
        stream_last_id = _redis_value(stream_info, "last-generated-id")
        blocked = await _group_holding_back_trim(
            redis_client, stream, observed, stream_last_id, maxlen
        )
        if blocked is None:
            return maxlen
        group_name, reason = blocked
        return self._relaxed_maxlen(stream, reason=reason, group=group_name)

    async def publish(self, stream: str, event: BaseModel | Mapping[str, Any]) -> None:
        broker = await self._get_broker()
        payload = (
            event.model_dump(mode="json")
            if isinstance(event, BaseModel)
            else dict(event)
        )
        redis_client = getattr(broker, "_connection", None)
        if redis_client is None:
            raise ConnectionError("Redis message bus has no active connection")
        async with asyncio.timeout(
            event_transport_settings.event_publish_timeout_seconds
        ):
            # XGROUP must succeed before XADD. If this times out after an
            # ambiguous XADD, the outbox retries and inbox idempotency contains
            # the duplicate.
            await ensure_stream_groups(redis_client, stream)
            maxlen = await self._safe_publish_maxlen(redis_client, stream)
            await broker.publish(payload, stream=stream, maxlen=maxlen)

    async def close(self) -> None:
        if not self._broker:
            return

        broker = self._broker
        self._broker = None
        try:
            await asyncio.wait_for(broker.stop(), timeout=5.0)
        except TimeoutError:
            logger.warning(
                "infrastructure.message_bus.timed_out_closing_faststream_redis.timeout"
            )


_message_bus: FastStreamRedisMessageBus | None = None


def get_message_bus() -> FastStreamRedisMessageBus:
    """Return shared message bus instance."""
    global _message_bus
    if _message_bus is None or _message_bus._redis_url != settings.redis_url:
        _message_bus = FastStreamRedisMessageBus(settings.redis_url)
    return _message_bus


async def close_message_bus() -> None:
    """Close shared message bus connection."""
    global _message_bus
    if _message_bus is None:
        return
    bus = _message_bus
    _message_bus = None
    await bus.close()
