from __future__ import annotations

import asyncio
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings


class RedisSurfaceEventDedupStore:
    """Redis-backed short-lived dedupe for external platform message delivery."""

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds or surface_settings.surface_event_dedupe_ttl_seconds
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis

        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                )
        return self._redis

    def _key(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_message_id: str,
    ) -> str:
        channel_key = external_channel_id or "none"
        surface_key = (
            str(surface_installation_id) if surface_installation_id else "unrouted"
        )
        return (
            "agent_surfaces:event_dedup:"
            f"{platform.lower()}:{surface_key}:{channel_key}:{external_message_id}"
        )

    async def claim_message(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> bool:
        del external_thread_id
        if not external_message_id:
            return True

        redis = await self._get_redis()
        claimed = await redis.set(
            self._key(
                surface_installation_id=surface_installation_id,
                platform=platform,
                external_channel_id=external_channel_id,
                external_message_id=external_message_id,
            ),
            "1",
            ex=self._ttl_seconds,
            nx=True,
        )
        return bool(claimed)

    async def close(self) -> None:
        if self._redis is None:
            return
        redis = self._redis
        self._redis = None
        if hasattr(redis, "aclose"):
            await redis.aclose()
        else:
            await redis.close()


_event_dedup_store: RedisSurfaceEventDedupStore | None = None


def get_surface_event_dedup_store() -> RedisSurfaceEventDedupStore:
    global _event_dedup_store
    if _event_dedup_store is None:
        _event_dedup_store = RedisSurfaceEventDedupStore()
    return _event_dedup_store


async def close_surface_event_dedup_store() -> None:
    global _event_dedup_store
    if _event_dedup_store is None:
        return
    store = _event_dedup_store
    _event_dedup_store = None
    await store.close()
