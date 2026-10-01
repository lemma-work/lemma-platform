"""One System One key, shared by every pod and process: a per-second budget.

System One documents its limit per key, and a deployment has one key. Without
a shared counter, one pod's batch of a thousand rows spends the second that a
person waiting on a reply needed. So the budget is split by lane: interactive
decisions may use all of it, ambient ones most of it, bulk ones only what is
left after headroom for the other two.

The counter is a fixed one-second window in Redis. Redis being down must not
stop decisions, so a failed count lets the request through and says so.
"""

from __future__ import annotations

import asyncio
import time

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.decisions.config import DecisionsSettings, decisions_settings
from app.modules.decisions.domain.deciders import Lane

logger = get_logger(__name__)

#: Share of the per-second budget each lane may reach before it waits.
LANE_SHARE: dict[Lane, float] = {
    Lane.INTERACTIVE: 1.0,
    Lane.AMBIENT: 0.9,
    Lane.BULK: 0.6,
}
#: How long each lane waits for a window with room before giving up on the rung.
LANE_PATIENCE_SECONDS: dict[Lane, float] = {
    Lane.INTERACTIVE: 0.25,
    Lane.AMBIENT: 5.0,
    Lane.BULK: 30.0,
}
_KEY_PREFIX = "decisions:system_one:window"


class SystemOneLimiter:
    def __init__(self, *, settings: DecisionsSettings = decisions_settings) -> None:
        self._settings = settings

    async def acquire(self, lane: Lane) -> bool:
        """Whether this request may go now, waiting up to the lane's patience."""
        ceiling = max(
            1, int(self._settings.typesafe_requests_per_second * LANE_SHARE[lane])
        )
        deadline = time.monotonic() + LANE_PATIENCE_SECONDS[lane]
        while True:
            window = int(time.time())
            try:
                count = await self._count(window)
            except RedisError:
                logger.warning(
                    "decisions.limiter.redis_unavailable.degraded", exc_info=True
                )
                return True
            if count <= ceiling:
                return True
            wait = (window + 1) - time.time()
            if time.monotonic() + wait > deadline:
                return False
            await asyncio.sleep(max(wait, 0.01))

    async def _count(self, window: int) -> int:
        redis = get_redis()
        key = f"{_KEY_PREFIX}:{window}"
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, 2)
            results = await pipe.execute()
        return int(results[0])
