from __future__ import annotations

import asyncio
from uuid import UUID

from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis

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
        self._ttl_seconds = (
            ttl_seconds or surface_settings.surface_event_dedupe_ttl_seconds
        )
        self._stranger_window_seconds = (
            surface_settings.surface_stranger_reply_window_seconds
        )
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis

        async with self._lock:
            if self._redis is None:
                self._redis = get_redis(url=self._redis_url)
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

    def _stranger_key(
        self,
        *,
        platform: str,
        surface_installation_id: UUID | None,
        sender_external_user_id: str,
    ) -> str:
        surface_key = (
            str(surface_installation_id) if surface_installation_id else "unrouted"
        )
        return (
            "agent_surfaces:stranger_reply:"
            f"{platform.lower()}:{surface_key}:{sender_external_user_id}"
        )

    async def claim_stranger_reply(
        self,
        *,
        platform: str,
        surface_installation_id: UUID | None,
        sender_external_user_id: str | None,
    ) -> bool:
        """One "here is how to get access" per sender per window, not per message.

        ``claim_message`` is keyed on the message id, so it stops a redelivery
        and nothing else: fifty messages from someone we cannot place earned
        fifty replies. On the shared number that is Lemma sending unsolicited
        messages from an address whose sender reputation every pod shares, and
        in a channel it is the same nudge, in public, every time that person
        speaks.

        A sender with no external id gets through, exactly as an event with no
        message id gets through ``claim_message`` -- there is no key to hold the
        window on, and message-level dedupe still applies.
        """
        if not sender_external_user_id:
            return True

        redis = await self._get_redis()
        claimed = await redis.set(
            self._stranger_key(
                platform=platform,
                surface_installation_id=surface_installation_id,
                sender_external_user_id=sender_external_user_id,
            ),
            "1",
            ex=self._stranger_window_seconds,
            nx=True,
        )
        return bool(claimed)

    async def release_message(
        self,
        *,
        surface_installation_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> None:
        del external_thread_id
        if not external_message_id:
            return

        redis = await self._get_redis()
        await redis.delete(
            self._key(
                surface_installation_id=surface_installation_id,
                platform=platform,
                external_channel_id=external_channel_id,
                external_message_id=external_message_id,
            )
        )

    async def close(self) -> None:
        # The client is shared process-wide; closing it here would break
        # every other component still using the same pool. Disposal is
        # close_redis_clients()'s job at lifespan shutdown.
        self._redis = None


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
