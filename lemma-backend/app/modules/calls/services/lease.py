"""One connection holds a call at a time, counted across API processes.

A reconnect -- a dropped network, a second tab, a phone switching to wifi --
used to leave two sessions on one call: two voices, both speaking every relay,
both routing turns, and the old one writing its transcript after the new one
had read the call's past. Now the newest connection takes the call: it names
itself holder, the old session sees it no longer is and ends, and the new one
waits (briefly) for the old to finish writing before it reads anything back.

Two keys per call: who holds it, and, per holder, that its session is still
running. Both lapse, so a process that dies frees the call by itself. Redis
being unreachable admits the connection, as a call slot does.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings as core_settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)

LEASE_TTL_SECONDS = 90
#: How long a new connection waits for the one it took over to finish.
HANDOVER_SECONDS = 5.0
_POLL_SECONDS = 0.1


def _holder_key(call_id: UUID) -> str:
    return f"calls:lease:{call_id}"


def _running_key(call_id: UUID, holder: str) -> str:
    return f"calls:lease:{call_id}:{holder}"


class CallLease:
    """This connection's claim on a call."""

    def __init__(
        self,
        call_id: UUID,
        *,
        redis: Redis | None = None,
        handover_seconds: float = HANDOVER_SECONDS,
    ) -> None:
        self.call_id = call_id
        self.holder = str(uuid4())
        self._client = redis
        self._handover_seconds = handover_seconds

    def _redis(self) -> Redis:
        return self._client or get_redis(url=core_settings.redis_url)

    async def take(self) -> None:
        """Hold the call, and wait for a session it took over to wind down."""
        redis = self._redis()
        try:
            previous = await redis.set(
                _holder_key(self.call_id),
                self.holder,
                ex=LEASE_TTL_SECONDS,
                get=True,
            )
            await redis.set(
                _running_key(self.call_id, self.holder), "1", ex=LEASE_TTL_SECONDS
            )
        except RedisError, OSError:
            logger.warning("calls.lease.take_unavailable.degraded", exc_info=True)
            return
        if previous is None:
            return
        previous = previous.decode() if isinstance(previous, bytes) else str(previous)
        deadline = time.monotonic() + self._handover_seconds
        while time.monotonic() < deadline:
            try:
                if not await redis.exists(_running_key(self.call_id, previous)):
                    return
            except RedisError, OSError:
                return
            await asyncio.sleep(_POLL_SECONDS)
        logger.info("calls.lease.handover_waited.observed", call_id=str(self.call_id))

    async def still_held(self) -> bool:
        """Whether this connection still holds the call, renewing it if so.

        Compared and renewed in one script: read then written apart, a holder
        that had just been replaced could write its own name back and evict
        the connection that replaced it. A lapsed key is not reclaimed -- the
        call has moved on, or the process stalled past the lease. Unknown
        (Redis unreachable) counts as held: a call is not ended for an outage
        of the thing that guards it."""
        redis = self._redis()
        try:
            held = await redis.eval(
                _RENEW,
                2,
                _holder_key(self.call_id),
                _running_key(self.call_id, self.holder),
                self.holder,
                LEASE_TTL_SECONDS,
            )
        except RedisError, OSError:
            logger.warning("calls.lease.renew_unavailable.degraded", exc_info=True)
            return True
        return int(held) == 1

    async def release(self) -> None:
        """Done: the session has ended and written what it had to write. The
        call is let go only if this connection still holds it, in one script,
        so a newer holder's claim is never deleted."""
        redis = self._redis()
        try:
            await redis.eval(
                _RELEASE,
                2,
                _holder_key(self.call_id),
                _running_key(self.call_id, self.holder),
                self.holder,
            )
        except RedisError, OSError:
            logger.warning("calls.lease.release_unavailable.degraded", exc_info=True)


# Renew only while this holder still holds the call.
_RENEW = """
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then return 0 end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[2]))
return 1
"""

# Let go of the call only if this holder still holds it; its running mark
# always goes, since its session is over either way.
_RELEASE = """
redis.call('DEL', KEYS[2])
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
return 1
"""
