"""How many calls one person may hold at once, counted across API processes.

A sorted set per person whose members are call sockets scored by when their
claim lapses. An open call refreshes its claim; a process that dies stops
counting once the claim lapses, so a crash cannot lock anyone out.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from redis.exceptions import RedisError

from app.core.config import settings as core_settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)

SLOT_TTL_SECONDS = 90.0
_REFRESH_SECONDS = 30.0

# Drop lapsed claims, then take one if fewer than the limit are held.
_CLAIM = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


def _key(user_id: UUID) -> str:
    return f"calls:slots:{user_id}"


@asynccontextmanager
async def call_slot(user_id: UUID, *, limit: int) -> AsyncIterator[bool]:
    """Hold one of the person's call slots for the block; yields False when
    they already hold ``limit``. Redis being unreachable admits the call."""
    member = str(uuid4())
    redis = get_redis(url=core_settings.redis_url)
    now = time.time()
    try:
        taken = await redis.eval(
            _CLAIM,
            1,
            _key(user_id),
            now,
            now + SLOT_TTL_SECONDS,
            limit,
            member,
            int(SLOT_TTL_SECONDS) + 1,
        )
    except RedisError, OSError:
        logger.warning("calls.slots.claim_unavailable.degraded", exc_info=True)
        taken = 1
    if int(taken) != 1:
        yield False
        return
    refresher = create_inherited_task(
        _refresh(user_id, member), name="calls.slots.refresh"
    )
    try:
        yield True
    finally:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher
        with contextlib.suppress(RedisError, OSError):
            await redis.zrem(_key(user_id), member)


async def _refresh(user_id: UUID, member: str) -> None:
    redis = get_redis(url=core_settings.redis_url)
    while True:
        await asyncio.sleep(_REFRESH_SECONDS)
        try:
            await redis.zadd(
                _key(user_id), {member: time.time() + SLOT_TTL_SECONDS}, xx=True
            )
            await redis.expire(_key(user_id), int(SLOT_TTL_SECONDS) + 1)
        except RedisError, OSError:
            logger.warning("calls.slots.refresh_unavailable.degraded", exc_info=True)
