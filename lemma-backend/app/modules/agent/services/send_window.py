"""The same words from the same person, moments apart, are one message.

A client id catches a send retried by the client that made it. It cannot catch
the same message sent twice by two things -- two tabs, a double tap on a phone
that sends before the button disables, a surface delivering one message twice
under two ids -- and each of those became a second message, answered twice or
starting a second run. So a send is also remembered for a short window by who
sent it, where, and a hash of what it said, and a repeat inside the window gets
the first one's run back with ``deduplicated`` set, which a client can show as
"already on it".

Kept in Redis because the window has to hold across API replicas, and fails
open: a Redis outage must not stop anybody sending.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunStartResult

logger = get_logger(__name__)

#: How long a send is remembered. Long enough for a double tap or a second
#: tab; short enough that saying the same thing again later is a new message.
WINDOW_SECONDS = 15

#: Held while the first send is still being stored, so a repeat that arrives
#: in that gap waits for its result instead of racing it.
_PENDING = "pending"
_WAIT_FOR_FIRST_SECONDS = 3.0
_WAIT_STEP_SECONDS = 0.1

_cache: RedisJsonCache[str] | None = None


def _window_cache() -> RedisJsonCache[str]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="agent:send-window",
            ttl_seconds=WINDOW_SECONDS,
        )
    return _cache


def window_key(
    *,
    user_id: UUID,
    content: str,
    conversation_id: UUID | None,
    pod_id: UUID,
    agent_name: str | None,
) -> str:
    """Who said it, where, and what. A new conversation is keyed by pod and agent."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    where = (
        f"conversation:{conversation_id}"
        if conversation_id is not None
        else f"pod:{pod_id}:agent:{agent_name or ''}"
    )
    return f"{user_id}:{where}:{digest}"


class SendWindow:
    """Starts a send once per window; a repeat gets the first one's result."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        cache: RedisJsonCache[str] | None = None,
    ) -> None:
        # The caller's session, handed back to the pool around each Redis round
        # trip: a send arrives with its authorization already read, and holding
        # that connection across Redis is what the session-scope gate forbids.
        self._session = session
        self._cache = cache

    @property
    def cache(self) -> RedisJsonCache[str]:
        return self._cache or _window_cache()

    async def start_send_once(
        self,
        key: str,
        start: Callable[[], Awaitable[AgentRunStartResult]],
    ) -> AgentRunStartResult:
        try:
            async with connection_released(self._session):
                first = await self.cache.set_raw_if_absent(key, _PENDING)
        except RedisError, OSError:
            logger.warning("agent.send_window.unavailable.degraded", exc_info=True)
            return await start()
        if not first:
            async with connection_released(self._session):
                earlier = await self._result_of_first_send(key)
            if earlier is not None:
                return earlier.model_copy(
                    update={"started_new_run": False, "deduplicated": True}
                )
            # The first send failed, or is taking longer than a person would
            # wait: this one goes ahead rather than being dropped.
            return await start()
        try:
            result = await start()
        except BaseException:
            async with connection_released(self._session):
                await self._forget_pending_send(key)
            raise
        try:
            async with connection_released(self._session):
                await self.cache.set_raw(key, result.model_dump_json())
        except RedisError, OSError:
            logger.warning("agent.send_window.unavailable.degraded", exc_info=True)
        return result

    async def _result_of_first_send(self, key: str) -> AgentRunStartResult | None:
        waited = 0.0
        while True:
            try:
                stored = await self.cache.get_raw(key)
            except RedisError, OSError:
                return None
            if stored is None:
                return None
            if stored != _PENDING:
                try:
                    return AgentRunStartResult.model_validate_json(stored)
                except ValidationError:
                    return None
            if waited >= _WAIT_FOR_FIRST_SECONDS:
                return None
            await asyncio.sleep(_WAIT_STEP_SECONDS)
            waited += _WAIT_STEP_SECONDS

    async def _forget_pending_send(self, key: str) -> None:
        """Let a retry of a send that failed go through at once."""
        try:
            await self.cache.delete_if_value(key, _PENDING)
        except RedisError, OSError:
            logger.warning("agent.send_window.unavailable.degraded", exc_info=True)
