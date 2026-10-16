"""How much one client may hold open on a pod's live socket.

Two limits: subscriptions per socket (checked in process, on the socket) and
sockets per user per pod (counted across every API process in Redis). Both
come through ``LiveLimitPort`` so a plan-backed provider can replace the
settings-backed default at startup, the same seam work limits use.

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
from app.modules.datastore.config_live import LiveSettings, live_settings

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
        logger.warning("datastore.live.limit_overrides_unparseable")
        return {}
    overrides: dict[str, tuple[int | None, int | None]] = {}
    if not isinstance(parsed, list):
        logger.warning("datastore.live.limit_overrides_unparseable")
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
        await redis.zremrangebyscore(key, "-inf", now)
        held = await redis.zcard(key)
        if held >= limit:
            return False
        await redis.zadd(key, {connection_id: now + SLOT_TTL_SECONDS})
        await redis.expire(key, int(SLOT_TTL_SECONDS) + 1)
    except (RedisError, OSError) as exc:
        logger.warning(
            "datastore.live.socket_slot_unavailable", error_type=type(exc).__name__
        )
        return True
    return True


async def refresh_socket_slot(pod_id: UUID, user_id: UUID, connection_id: str) -> None:
    key = _slots_key(pod_id, user_id)
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zadd(key, {connection_id: time.time() + SLOT_TTL_SECONDS}, xx=True)
        await redis.expire(key, int(SLOT_TTL_SECONDS) + 1)
    except (RedisError, OSError) as exc:
        logger.warning(
            "datastore.live.socket_slot_unavailable", error_type=type(exc).__name__
        )


async def release_socket_slot(pod_id: UUID, user_id: UUID, connection_id: str) -> None:
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zrem(_slots_key(pod_id, user_id), connection_id)
    except (RedisError, OSError) as exc:
        logger.warning(
            "datastore.live.socket_slot_unavailable", error_type=type(exc).__name__
        )
