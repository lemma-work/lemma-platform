"""How many decisions one organization may ask a minute.

A clock-aligned fixed window in Redis: cheap, and the Retry-After it gives is
exact. It fails open. This limit protects capacity -- a runaway loop in one
organization's function should not use up a shared provider's rate for every
other -- and spend is already enforced, closed, by the usage ledger. Refusing
every decision because Redis blinked would take voice routing down with it.

The Redis call is bounded well under the client's own read timeout, because an
interactive decision has seconds in total and must not spend them waiting here.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Protocol

from redis.exceptions import RedisError

from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 60
_REDIS_BUDGET_SECONDS = 0.25


class _Evaluates(Protocol):
    async def eval(
        self, script: str, numkeys: int, *keys_and_args: object
    ) -> object: ...


class OrganizationRateLimiter:
    def __init__(
        self,
        *,
        limit_per_minute: int,
        redis: _Evaluates | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._limit = limit_per_minute
        self._redis = redis
        self._clock = clock

    async def retry_after(self, key: str) -> int | None:
        if self._limit <= 0:
            return None
        now = self._clock()
        window = int(now // _WINDOW_SECONDS)
        try:
            async with asyncio.timeout(_REDIS_BUDGET_SECONDS):
                count = await incr_with_ttl(
                    self._client(),
                    f"decisions:rate:{key}:{window}",
                    _WINDOW_SECONDS * 2,
                )
        except RedisError, TimeoutError:
            logger.warning("decisions.rate_limit.unavailable.degraded", exc_info=True)
            return None
        if count <= self._limit:
            return None
        return max(1, math.ceil((window + 1) * _WINDOW_SECONDS - now))

    def _client(self) -> _Evaluates:
        if self._redis is not None:
            return self._redis
        from app.core.infrastructure.redis.client import get_redis

        return get_redis()
