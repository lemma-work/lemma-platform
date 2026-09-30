"""Fixed-window request counting in Redis.

Fails open. A rate limit protects the service from a client; it is not an
authorization check, and refusing every MCP request because Redis blinked would
turn a Redis outage into an outage of every connected client.
"""

from __future__ import annotations

from typing import Protocol

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)

_COUNT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {current, redis.call('TTL', KEYS[1])}
"""


class _Evaluates(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...


class RateLimiter:
    def __init__(self, redis: _Evaluates | None = None) -> None:
        self._fixed = redis

    @property
    def _redis(self) -> _Evaluates:
        return self._fixed or get_redis()

    async def retry_after(
        self, key: str, *, limit: int, window_seconds: int
    ) -> int | None:
        """Seconds to wait if this request is over the limit, else ``None``."""
        try:
            result = await self._redis.eval(
                _COUNT, 1, f"mcp_access:rate:{key}", str(window_seconds)
            )
        except RedisError:
            logger.warning("mcp_access.rate_limit.unavailable.degraded", exc_info=True)
            return None
        if not isinstance(result, list) or len(result) != 2:
            return None
        count, ttl = int(result[0]), int(result[1])
        return max(1, ttl) if count > limit else None
