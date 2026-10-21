"""How much one client may hold open on a pod's live socket.

Two limits: subscriptions per socket (checked in process, on the socket) and
sockets per user per pod (counted across every API process in Redis). Both
come through ``LiveLimitPort`` so a plan-backed provider can replace the
settings-backed default at startup, the same seam work limits use. A call may
also be limited per user across their sockets (``admit_user_call``), so
opening more sockets does not buy more of it.

The socket count is a sorted set per (pod, user) whose members are connection
ids scored by when their claim lapses. A socket refreshes its claim while it is
open; one that dies without saying so stops counting once its claim lapses,
so a crashed process cannot lock a user out.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings as core_settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.live.config import LiveSettings, live_settings

logger = get_logger(__name__)

#: How long a socket's claim on a slot lasts without a refresh.
SLOT_TTL_SECONDS = 90.0
#: How often an open socket refreshes its claim.
SLOT_REFRESH_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class LiveLimits:
    subscriptions_per_socket: int
    sockets_per_user: int


class LiveLimitPort(Protocol):
    async def limits_for(self, pod_id: UUID) -> LiveLimits: ...


@lru_cache(maxsize=8)
def _parse_overrides(raw: str) -> dict[str, tuple[int | None, int | None]]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("live.limit_overrides_unparseable.degraded")
        return {}
    overrides: dict[str, tuple[int | None, int | None]] = {}
    if not isinstance(parsed, list):
        logger.warning("live.limit_overrides_unparseable.degraded")
        return {}
    for entry in parsed:
        if not isinstance(entry, dict) or not isinstance(entry.get("pod_id"), str):
            continue
        subs = entry.get("subscriptions_per_socket")
        sockets = entry.get("sockets_per_user")
        overrides[entry["pod_id"].lower()] = (
            subs if isinstance(subs, int) and subs > 0 else None,
            sockets if isinstance(sockets, int) and sockets > 0 else None,
        )
    return overrides


class ConfiguredLiveLimitPort:
    def __init__(self, config: LiveSettings | None = None) -> None:
        self._config = config or live_settings

    async def limits_for(self, pod_id: UUID) -> LiveLimits:
        subs, sockets = _parse_overrides(self._config.live_limit_overrides_json).get(
            str(pod_id), (None, None)
        )
        return LiveLimits(
            subscriptions_per_socket=subs
            or self._config.live_max_subscriptions_per_socket,
            sockets_per_user=sockets or self._config.live_max_sockets_per_user_per_pod,
        )


_port: LiveLimitPort | None = None


def install_live_limit_port(port: LiveLimitPort) -> None:
    global _port
    _port = port


def reset_live_limit_port() -> None:
    global _port
    _port = None


def current_live_limit_port() -> LiveLimitPort:
    return _port or ConfiguredLiveLimitPort()


def _slots_key(pod_id: UUID, user_id: UUID) -> str:
    return f"live:sockets:{pod_id}:{user_id}"


#: Lapsed claims dropped, the rest counted, and this one added -- or not, past
#: the limit -- in one step: two sockets opening together cannot both see a
#: free slot and both take it.
_CLAIM_SLOT_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZSCORE', KEYS[1], ARGV[4]) == false
    and redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


async def claim_socket_slot(
    pod_id: UUID, user_id: UUID, connection_id: str, limit: int
) -> bool:
    """Take one of the user's socket slots on the pod; False when all are held.

    Redis being unreachable admits the socket: the limit protects capacity,
    and refusing every socket during a Redis blip would take live pages down
    for a limit nobody was near.
    """
    key = _slots_key(pod_id, user_id)
    now = time.time()
    redis = get_redis(url=core_settings.redis_url)
    try:
        taken = await redis.eval(
            _CLAIM_SLOT_LUA,
            1,
            key,
            now,
            now + SLOT_TTL_SECONDS,
            limit,
            connection_id,
            int(SLOT_TTL_SECONDS) + 1,
        )
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.socket_slot_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return True
    return int(taken) == 1


async def refresh_socket_slot(pod_id: UUID, user_id: UUID, connection_id: str) -> None:
    key = _slots_key(pod_id, user_id)
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zadd(key, {connection_id: time.time() + SLOT_TTL_SECONDS}, xx=True)
        await redis.expire(key, int(SLOT_TTL_SECONDS) + 1)
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.socket_slot_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def release_socket_slot(pod_id: UUID, user_id: UUID, connection_id: str) -> None:
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zrem(_slots_key(pod_id, user_id), connection_id)
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.socket_slot_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def slots_held(pod_id: UUID, user_id: UUID) -> int:
    """How many sockets the user holds open on the pod now; 0 if unknown."""
    key = _slots_key(pod_id, user_id)
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zremrangebyscore(key, "-inf", time.time())
        return int(await redis.zcard(key))
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.socket_slot_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return 0


def _calls_key(pod_id: UUID, user_id: UUID, method: str, minute: int) -> str:
    return f"live:calls:{method}:{pod_id}:{user_id}:{minute}"


async def admit_user_call(
    pod_id: UUID, user_id: UUID, method: str, per_minute: int
) -> bool:
    """Count one ``method`` call by the user on the pod this minute, across
    every socket and process; False once past ``per_minute``.

    A fixed window per wall-clock minute: at most twice the limit can land
    across a boundary, which is all an ephemeral call's limit needs. Redis
    being unreachable admits the call, as a socket slot is admitted.
    """
    minute = int(time.time() // 60)
    key = _calls_key(pod_id, user_id, method, minute)
    redis = get_redis(url=core_settings.redis_url)
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, 120)
            count, _ = await pipe.execute()
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.call_limit_unavailable.degraded",
            method=method,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return True
    return int(count) <= per_minute
