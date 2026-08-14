"""A Redis cache that answers "miss" instead of raising.

Every cache in front of a database read wants the same behaviour: a Redis
outage must slow the request down, never fail it, and a sustained outage must
be visible before it turns into database pressure. Written out per cache that is
three copies of the same try/except/record dance, and the third copy is where
one of them quietly stops recording.

The read path returns ``None`` for a miss, an outage, or a payload this version
cannot parse — all three mean the same thing to a caller: go get it from the
source.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)

T = TypeVar("T")


class ResilientJsonCache:
    """A named cache whose failures are recorded rather than raised."""

    def __init__(self, *, name: str, key_prefix: str, redis_url: str, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._cache = RedisJsonCache(
            redis_url=redis_url, key_prefix=key_prefix, ttl_seconds=ttl_seconds
        )
        self._incident = DependencyIncident(name, logger=logger)

    async def get(self, suffix: str, decode: Callable[[str], T]) -> T | None:
        """Decoded value, or None on a miss, an outage, or an unreadable payload."""
        try:
            payload = await self._cache.get_raw(suffix)
        except Exception as exc:
            self._incident.record_failure(error_type=type(exc).__name__)
            return None
        self._incident.record_success()
        if not payload:
            return None
        try:
            return decode(payload)
        except Exception:
            # A payload written by another version is a miss, not an error: the
            # caller rebuilds it and overwrites this one.
            return None

    async def set(self, suffix: str, payload: str) -> None:
        try:
            await self._cache.set_raw(suffix, payload)
        except Exception as exc:
            self._incident.record_failure(error_type=type(exc).__name__)
        else:
            self._incident.record_success()

    async def delete(self, suffix: str) -> None:
        try:
            await self._cache.delete(suffix)
        except Exception as exc:
            self._incident.record_failure(error_type=type(exc).__name__)
        else:
            self._incident.record_success()
