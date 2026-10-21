"""Who in a pod has a live socket open: the ``presence`` call.

A sorted set per pod in Redis, ``live:presence:<pod>``, of user ids scored by
when their claim lapses. A socket claims its user on connect and again each
time it refreshes its slot (``limits.SLOT_REFRESH_SECONDS``); a user whose
last socket closes is scored "lapsed now", and one whose process died without
saying so lapses after ``limits.SLOT_TTL_SECONDS``. A lapsed user is kept for
``SEEN_FOR_SECONDS``, so ``seen_within`` can say who was in the app a moment
ago -- channels holds back a push they would read there anyway. Redis being
unreachable answers "nobody", never an error: presence decorates a page and
spares a push, it gates nothing.
"""

from __future__ import annotations

import time
from collections.abc import Collection
from uuid import UUID

from pydantic import JsonValue
from redis.exceptions import RedisError

from app.core.config import settings as core_settings
from app.core.infrastructure.redis.client import get_redis
from app.core.live.connection import LiveConnection
from app.core.live.limits import SLOT_TTL_SECONDS, slots_held
from app.core.live.pod_access import require_kept_pod_access
from app.core.live.protocol import JsonObject
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The most people one ``presence`` answer names.
MAX_PRESENT = 500
#: How long a user whose sockets lapsed is remembered as seen.
SEEN_FOR_SECONDS = 15 * 60


def _key(pod_id: UUID) -> str:
    return f"live:presence:{pod_id}"


async def mark_online(pod_id: UUID, user_id: UUID) -> None:
    key = _key(pod_id)
    redis = get_redis(url=core_settings.redis_url)
    at = time.time()
    try:
        await redis.zadd(key, {str(user_id): at + SLOT_TTL_SECONDS})
        await redis.zremrangebyscore(key, "-inf", at - SEEN_FOR_SECONDS)
        await redis.expire(key, int(SLOT_TTL_SECONDS + SEEN_FOR_SECONDS) + 1)
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def mark_offline(pod_id: UUID, user_id: UUID) -> None:
    """The user is no longer present -- seen just now -- unless another socket
    of theirs is open."""
    if await slots_held(pod_id, user_id) > 0:
        return
    redis = get_redis(url=core_settings.redis_url)
    at = time.time()
    try:
        await redis.zadd(_key(pod_id), {str(user_id): at}, xx=True)
        await redis.zremrangebyscore(_key(pod_id), "-inf", at - SEEN_FOR_SECONDS)
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def online(pod_id: UUID) -> list[UUID]:
    redis = get_redis(url=core_settings.redis_url)
    try:
        members = await redis.zrangebyscore(
            _key(pod_id), time.time(), "+inf", start=0, num=MAX_PRESENT
        )
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return []
    found: list[UUID] = []
    for member in members:
        raw = member.decode() if isinstance(member, bytes) else str(member)
        try:
            found.append(UUID(raw))
        except ValueError:
            continue
    return found


async def seen_within(
    pod_id: UUID, user_ids: Collection[UUID], *, seconds: float
) -> set[UUID]:
    """Which of ``user_ids`` had the app open in the last ``seconds`` -- or
    have it open now. Nobody, when Redis cannot say."""
    wanted = sorted(set(user_ids))
    if not wanted:
        return set()
    redis = get_redis(url=core_settings.redis_url)
    try:
        scores = await redis.zmscore(_key(pod_id), [str(user) for user in wanted])
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return set()
    since = time.time() - seconds
    return {
        user
        for user, score in zip(wanted, scores, strict=True)
        if score is not None and float(score) >= since
    }


class PresenceCall:
    """``presence``: the pod's members with a live socket open, by user id."""

    name = "presence"

    def per_minute(self) -> int:
        return 30

    async def __call__(
        self, connection: LiveConnection, params: JsonObject, *, call_id: str
    ) -> JsonValue:
        # Who is in the pod is for who still is: one taken out of it is
        # refused, and their socket closed.
        await require_kept_pod_access(connection)
        return {"online": [str(user_id) for user_id in await online(connection.pod_id)]}


__all__ = ["PresenceCall", "mark_offline", "mark_online", "online", "seen_within"]
