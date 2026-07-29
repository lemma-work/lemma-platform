from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, Generic, TypeVar, cast
from weakref import WeakSet

from redis.asyncio import Redis


T = TypeVar("T")
_live_caches: WeakSet["RedisJsonCache[Any]"] = WeakSet()
_DELETE_IF_VALUE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RedisJsonCache(Generic[T]):
    def __init__(self, redis_url: str, key_prefix: str, ttl_seconds: int):
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()
        _live_caches.add(self)

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

    async def set_raw(
        self, suffix: str, payload: str, *, ttl_seconds: int | None = None
    ) -> None:
        redis = await self._get_redis()
        await redis.set(
            self.build_key(suffix),
            payload,
            ex=ttl_seconds if ttl_seconds is not None else self._ttl_seconds,
        )

    async def set_raw_if_absent(
        self,
        suffix: str,
        payload: str,
        *,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Atomically acquire a namespaced, expiring value."""
        redis = await self._get_redis()
        result = await redis.set(
            self.build_key(suffix),
            payload,
            ex=ttl_seconds if ttl_seconds is not None else self._ttl_seconds,
            nx=True,
        )
        return bool(result)

    async def delete_if_value(self, suffix: str, expected: str) -> bool:
        """Delete a namespaced value only when it is still owned by ``expected``."""
        redis = await self._get_redis()
        deleted = await cast(
            Awaitable[Any],
            redis.eval(
                _DELETE_IF_VALUE_SCRIPT,
                1,
                self.build_key(suffix),
                expected,
            ),
        )
        return bool(deleted)

    async def get_json(self, suffix: str) -> Any | None:
        raw = await self.get_raw(suffix)
        return json.loads(raw) if raw is not None else None

    async def set_json(
        self, suffix: str, value: Any, *, ttl_seconds: int | None = None
    ) -> None:
        await self.set_raw(suffix, json.dumps(value), ttl_seconds=ttl_seconds)

    async def delete(self, suffix: str) -> None:
        redis = await self._get_redis()
        await redis.delete(self.build_key(suffix))

    async def delete_prefix(self, sub_prefix: str) -> None:
        """Delete every key under ``{key_prefix}:{sub_prefix}*`` (SCAN + DEL).

        Narrower than :meth:`clear_prefix`: lets an invalidation target a single
        logical group (e.g. all of one principal's role snapshots across orgs and
        pods) instead of flushing the whole cache. O(matched keys).
        """
        redis = await self._get_redis()
        pattern = f"{self._key_prefix}:{sub_prefix}*"
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=256)
            if keys:
                await redis.delete(*keys)
            if cursor == 0:
                break

    async def clear_prefix(self) -> None:
        """Delete every key under this cache's prefix (SCAN + DEL). Used by
        invalidation hooks and test isolation; O(matched keys)."""
        await self.delete_prefix("")

    async def close(self) -> None:
        async with self._lock:
            redis = self._redis
            self._redis = None
        if redis is None:
            return
        if hasattr(redis, "aclose"):
            await redis.aclose()
        else:
            await redis.close()


async def close_redis_json_caches() -> None:
    """Close every live process-local cache client during service teardown."""

    results = await asyncio.gather(
        *(cache.close() for cache in tuple(_live_caches)),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        raise failures[0]
