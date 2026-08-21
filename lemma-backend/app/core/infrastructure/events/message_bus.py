"""Shared FastStream Redis message bus resource."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any
from pydantic import BaseModel
from faststream.redis import RedisBroker

from app.core.config import settings
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import (
    ensure_stream_groups,
    registered_groups_for_stream,
)
from app.core.log.log import get_dependency_logger, get_logger

logger = get_logger(__name__)


def _redis_value(mapping: dict[Any, Any], name: str, default: Any = None) -> Any:
    return mapping.get(name, mapping.get(name.encode(), default))


def _last_delivered_id(group: dict[Any, Any]) -> Any:
    # Redis client adapters normalize this wire field under two common names.
    return _redis_value(
        group,
        "last-delivered-id",
        _redis_value(group, "last-delivered-message-id"),
    )


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
                        logger.debug(
                            "infrastructure.message_bus.closing_cancelled_redis_connection.diagnostic"
                        )
                    raise
                except Exception:
                    try:
                        await broker.stop()
                    except Exception:
                        logger.debug(
                            "infrastructure.message_bus.closing_partial_redis_connection.diagnostic"
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

    async def _safe_publish_maxlen(self, redis_client, stream: str) -> int | None:
        """Choose MAXLEN only when it cannot trim pending or unread work.

        FastStream's MAXLEN option does not account for consumer progress.
        Ungrouped streams are explicitly ephemeral and may be capped directly.
        Grouped streams require zero pending deliveries and retain at least
        twice the largest reported lag. Unknown group state fails safe by
        publishing without a cap; the stream snapshot exposes that backlog.
        """
        maxlen = event_transport_settings.stream_maxlen_for(stream)
        if maxlen is None:
            return None
        declared_groups = registered_groups_for_stream(stream)
        try:
            groups = await redis_client.xinfo_groups(stream)
        except Exception:
            # A not-yet-created stream has no group state to protect.
            return maxlen if not await redis_client.exists(stream) else None
        observed = {}
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
        if not declared_groups <= observed.keys():
            return None
        if not observed:
            return maxlen
        stream_info = await redis_client.xinfo_stream(stream)
        stream_last_id = _redis_value(stream_info, "last-generated-id")
        # Extra groups may be obsolete, but they still own pending/unread work.
        # Never trim through them implicitly; cleanup is an explicit operation.
        for group in observed.values():
            pending = _redis_value(group, "pending")
            lag = _redis_value(group, "lag")
            last_delivered = _last_delivered_id(group)
            caught_up = pending == 0 and last_delivered == stream_last_id
            if pending != 0 or (
                not caught_up and (not isinstance(lag, int) or lag > maxlen // 2)
            ):
                return None
        return maxlen

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
