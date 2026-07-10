"""Shared FastStream Redis message bus resource."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from pydantic import BaseModel
from faststream.redis import RedisBroker

from app.core.config import settings
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.stream_subscriber import ensure_stream_groups

logger = logging.getLogger(__name__)


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
                broker = RedisBroker(self._redis_url)
                try:
                    await broker.connect()
                except asyncio.CancelledError:
                    try:
                        await broker.stop()
                    except Exception:
                        logger.warning("Failed closing cancelled Redis connection")
                    raise
                except Exception:
                    try:
                        await broker.stop()
                    except Exception:
                        logger.warning("Failed closing partial Redis connection")
                    raise
                self._broker = broker
        return self._broker

    async def connect(self) -> RedisBroker:
        """Eagerly initialize the shared broker connection."""
        return await self._get_broker()

    async def publish(
        self, stream: str, event: BaseModel | Mapping[str, Any]
    ) -> None:
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
            await broker.publish(payload, stream=stream)

    async def close(self) -> None:
        if not self._broker:
            return

        broker = self._broker
        self._broker = None
        try:
            await asyncio.wait_for(broker.stop(), timeout=5.0)
        except TimeoutError:
            logger.warning("Timed out closing FastStream Redis message bus")


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
