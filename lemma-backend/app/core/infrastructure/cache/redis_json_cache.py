from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

from redis.asyncio import Redis


T = TypeVar("T")


class RedisJsonCache(Generic[T]):
    def __init__(self, redis_url: str, key_prefix: str, ttl_seconds: int):
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
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

    def build_key(self, suffix: str) -> str:
        return f"{self._key_prefix}:{suffix}"

    async def get_raw(self, suffix: str) -> str | None:
        redis = await self._get_redis()
        return await redis.get(self.build_key(suffix))

    async def set_raw(self, suffix: str, payload: str) -> None:
        redis = await self._get_redis()
        await redis.set(
            self.build_key(suffix),
            payload,
            ex=self._ttl_seconds,
        )

    async def delete(self, suffix: str) -> None:
        redis = await self._get_redis()
        await redis.delete(self.build_key(suffix))

    async def clear_prefix(self) -> None:
        """Delete every key under this cache's prefix (SCAN + DEL). Used by
        invalidation hooks and test isolation; O(matched keys)."""
        redis = await self._get_redis()
        pattern = f"{self._key_prefix}:*"
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=256)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break

    async def close(self) -> None:
        if self._redis is None:
            return
        redis = self._redis
        self._redis = None
        if hasattr(redis, "aclose"):
            await redis.aclose()
        else:
            await redis.close()
