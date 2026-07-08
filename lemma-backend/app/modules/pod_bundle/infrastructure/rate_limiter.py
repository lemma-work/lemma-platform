"""Per-user daily rate limiting for pod-bundle export/import jobs.

Export and import kick off long-running worker jobs (archive assembly, agentbox
app builds, multi-resource apply). Without a cap a single account can enqueue
them without bound and starve the workers, so we count job *starts* per user per
UTC day in Redis and reject once the configured limit is hit.

Design notes:

- The counter is a plain ``INCR`` on a date-stamped key (``…:{YYYYMMDD}``) with a
  short TTL, so each UTC day gets its own self-expiring key — no cron cleanup and
  no stale reads across days. The same ``INCR``/``EXPIRE`` pattern the schedule
  circuit breaker uses (:mod:`app.modules.schedule.services.schedule_fire_store`).
- **Fails open**: a Redis blip must never block a legitimate export/import, so any
  Redis error is logged and treated as "under the limit". The cap is an abuse
  guard, not a correctness invariant.
- The increment happens on the *accepted* path only (the caller invokes this after
  authorization, before enqueue), so a rejected over-limit attempt still counts —
  which is the desired behavior: hammering the endpoint keeps you rejected rather
  than resetting your quota.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.errors import BundleRateLimitExceededError

logger = get_logger(__name__)

# Comfortably outlives a single UTC day (keys are date-stamped, so this is only
# for cleanup of a day's key after it stops being written).
_COUNTER_TTL_SECONDS = 2 * 24 * 60 * 60  # 48h


class BundleRateLimiter:
    """Redis-backed per-user daily counter for bundle export/import starts."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _key(operation: str, user_id: object) -> str:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"pod-bundle:ratelimit:{operation}:{user_id}:{day}"

    async def check_and_increment(
        self, *, user_id: object, operation: str, limit: int
    ) -> None:
        """Count one ``operation`` (``"export"``/``"import"``) start for ``user_id``
        and raise :class:`BundleRateLimitExceededError` if it exceeds ``limit``.

        A non-positive ``limit`` disables the cap. Fails open on any Redis error.
        """
        if limit <= 0:
            return
        try:
            redis = await self._get_redis()
            key = self._key(operation, user_id)
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, _COUNTER_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 — the cap is best-effort
            logger.warning(
                "Bundle rate-limit counter unavailable for %s %s (%s); allowing",
                operation,
                user_id,
                exc,
            )
            return
        if count > limit:
            raise BundleRateLimitExceededError(
                f"Daily {operation} limit reached ({limit} per day). "
                "Try again tomorrow (UTC) or ask an admin to raise the limit.",
                details={"operation": operation, "limit": limit},
            )


_limiter: BundleRateLimiter | None = None


def get_bundle_rate_limiter() -> BundleRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = BundleRateLimiter()
    return _limiter
