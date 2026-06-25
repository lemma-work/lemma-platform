from collections.abc import Sequence

import redis.asyncio as redis
from faststream.redis import RedisBroker
from pydantic import BaseModel

from app.core.config import settings
from app.core.infrastructure.events.stream_subscriber import (
    ensure_consumer_groups,
    ensure_named_groups,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class PubSubPublisher:
    """Publisher for pubsub."""

    def __init__(self):
        self.broker = RedisBroker(settings.redis_url)
        self._redis: redis.Redis | None = None

    async def __aenter__(self):
        await self.broker.start()
        self._redis = redis.from_url(settings.redis_url, decode_responses=False)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.broker.stop()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def publish(
        self,
        stream: str,
        data: BaseModel,
        *,
        ensure_groups: Sequence[str] | None = None,
    ):
        """Publish a message to a stream (for permanent events like triggers).

        Ensure the stream's consumer groups exist immediately before XADD. A
        grouped subscriber that loses its group (Redis flush/failover, or a
        transient delete) would otherwise miss this message entirely — the group,
        recreated later at ``$``, skips entries added while it was gone. Creating
        the group first (idempotent; BUSYGROUP keeps the existing group and its
        position) guarantees this event lands in a group that will deliver it.

        Pass ``ensure_groups`` with explicit group names when publishing from a
        process that does NOT import the consuming subscribers (scheduler/API
        pods) — the subscriber registry is empty there, so the names must be
        supplied. Otherwise the per-process registry is used.
        """
        if self._redis is not None:
            try:
                if ensure_groups:
                    await ensure_named_groups(self._redis, stream, ensure_groups)
                else:
                    await ensure_consumer_groups(
                        self._redis, warn_on_create=False, only_stream=stream
                    )
            except Exception as exc:  # noqa: BLE001 - never block publishing
                logger.warning(
                    "Failed ensuring consumer groups for stream %s before publish: %s",
                    stream,
                    exc,
                )
        await self.broker.publish(data, stream=stream)

    async def publish_channel(self, channel: str, data: BaseModel):
        """Publish a message to a channel (for realtime pubsub updates)."""
        await self.broker.publish(data, channel=channel)
