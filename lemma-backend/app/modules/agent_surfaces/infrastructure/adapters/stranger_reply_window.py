"""The stranger-reply window: a throttle, kept in Redis on purpose.

Message-level dedup is durable now (``surface_message_claims`` in Postgres, see
``services/message_claims``). What stays in Redis is the one key whose loss
costs nothing but a repeated nudge: "one 'here is how to get access' per
sender per window". A throttle, not a record -- if Redis forgets it, somebody
we cannot place is told how to get access twice.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.modules.agent_surfaces.config import surface_settings


class RedisStrangerReplyWindow:
    """One unsolicited "here is how to get access" per sender per window."""

    def __init__(self, *, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._window_seconds = surface_settings.surface_stranger_reply_window_seconds
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
        """Whether this sender may be told how to get access now.

        A sender with no external id gets through: there is no key to hold the
        window on, and message-level dedup still applies.
        """
        if not sender_external_user_id:
            return True
        redis = await self._get_redis()
        claimed = await redis.set(
            self._key(
                platform=platform,
                surface_installation_id=surface_installation_id,
                sender_external_user_id=sender_external_user_id,
            ),
            "1",
            ex=self._window_seconds,
            nx=True,
        )
        return bool(claimed)

    async def release_stranger_reply(
        self,
        *,
        platform: str,
        surface_installation_id: UUID | None,
        sender_external_user_id: str | None,
    ) -> None:
        if not sender_external_user_id:
            return
        redis = await self._get_redis()
        await redis.delete(
            self._key(
                platform=platform,
                surface_installation_id=surface_installation_id,
                sender_external_user_id=sender_external_user_id,
            )
        )

    async def close(self) -> None:
        # The client is shared process-wide; disposal is close_redis_clients()'s.
        self._redis = None


_window: RedisStrangerReplyWindow | None = None


def get_stranger_reply_window() -> RedisStrangerReplyWindow:
    global _window
    if _window is None:
        _window = RedisStrangerReplyWindow()
    return _window


async def close_stranger_reply_window() -> None:
    global _window
    if _window is None:
        return
    window, _window = _window, None
    await window.close()


__all__ = [
    "RedisStrangerReplyWindow",
    "close_stranger_reply_window",
    "get_stranger_reply_window",
]
