"""How fast a hook takes deliveries.

The same fixed windows in Redis that bound a web form's submissions
(``agent_surfaces/services/outsider_limits``), counted per hook: one a minute,
which turns a burst into retries, and one a day, which stops a sender stuck in
a loop from filling a table. It fails closed like that limiter does, because
what it guards is reachable with nothing but an address: a refused delivery is
answered with a status the sender retries on, so nothing is lost while Redis
is away.

Counted only once the address has proved itself, so nobody without the secret
can spend a hook's allowance.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.core.log.log import get_logger
from app.modules.datastore.config_hooks import hook_settings

logger = get_logger(__name__)

_MINUTE = 60
_DAY = 86_400


class HookAllowance(StrEnum):
    ALLOWED = "allowed"
    #: Over a window: the sender should slow down.
    LIMITED = "limited"
    #: The counters could not be read, so nothing is let through.
    UNAVAILABLE = "unavailable"


class HookLimiter:
    def __init__(self, *, redis=None) -> None:
        self._redis = redis

    async def allow(self, hook_id: UUID) -> HookAllowance:
        client = self._redis or get_redis()
        windows = (
            (
                f"hook:deliveries:minute:{hook_id}",
                _MINUTE,
                hook_settings.datastore_hook_deliveries_per_minute,
            ),
            (
                f"hook:deliveries:day:{hook_id}",
                _DAY,
                hook_settings.datastore_hook_deliveries_per_day,
            ),
        )
        charged: list[str] = []
        try:
            for key, ttl, limit in windows:
                count = await incr_with_ttl(client, key, ttl)
                charged.append(key)
                if count > limit:
                    # A refused delivery did not happen: the day keeps no
                    # charge for what the minute refused.
                    for spent in charged:
                        await client.decr(spent)
                    logger.info(
                        "datastore.hooks.limit_exceeded.observed",
                        hook_id=str(hook_id),
                        window=ttl,
                    )
                    return HookAllowance.LIMITED
        except (RedisError, OSError) as exc:
            logger.warning(
                "datastore.hooks.limits_unavailable.degraded",
                error_type=type(exc).__name__,
            )
            return HookAllowance.UNAVAILABLE
        return HookAllowance.ALLOWED
