"""What an ``Idempotency-Key`` on starting a WhatsApp group already started.

Starting a group calls Meta, so a retried request must not start a second one.
The key is held in Redis while the first request runs (``pending``) and names
the group it made afterwards, for a day: a retry with it answers with that
group, and one sent while the first still runs is told to wait. Without Redis
the start goes ahead unkeyed -- asking again for the same group by the same
member still answers from the row (``WhatsAppGroupOpener.request_group``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: How long a key names the group it started.
KEY_TTL_SECONDS = 24 * 60 * 60
#: How long a start may hold its key before a retry may take it: longer than
#: the request itself ever runs.
_PENDING_TTL_SECONDS = 120
_PENDING = "pending"

_cache: RedisJsonCache[str] | None = None


def _keys() -> RedisJsonCache[str]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surfaces:whatsapp-group-start",
            ttl_seconds=KEY_TTL_SECONDS,
        )
    return _cache


@dataclass(frozen=True, slots=True)
class Claim:
    """The first request with this key holds it (``held``); a later one gets
    the group it made (``started``), or neither while the first still runs."""

    suffix: str | None
    held: bool = False
    started: UUID | None = None

    @property
    def in_progress(self) -> bool:
        return self.suffix is not None and not self.held and self.started is None


def _suffix(pod_id: UUID, user_id: UUID, key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"{pod_id}:{user_id}:{digest}"


async def claim(*, pod_id: UUID, user_id: UUID, key: str | None) -> Claim:
    if key is None:
        return Claim(suffix=None)
    suffix = _suffix(pod_id, user_id, key)
    keys = _keys()
    value: str | None = None
    try:
        # Twice at most: a key that expires between the two reads names a
        # start long over, and the second try takes it.
        for _ in range(2):
            if await keys.set_raw_if_absent(
                suffix, _PENDING, ttl_seconds=_PENDING_TTL_SECONDS
            ):
                return Claim(suffix=suffix, held=True)
            known = await keys.get_raw(suffix)
            value = known.decode() if isinstance(known, bytes) else known
            if value is not None:
                break
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.group_start_keys.key_unavailable.degraded", exc_info=True
        )
        return Claim(suffix=None)
    if value is None or value == _PENDING:
        return Claim(suffix=suffix)
    try:
        return Claim(suffix=suffix, started=UUID(value))
    except ValueError:
        return Claim(suffix=suffix)


async def started(found: Claim, group_id: UUID) -> None:
    """The held key names the group it started."""
    if found.suffix is None or not found.held:
        return
    try:
        await _keys().set_raw(found.suffix, str(group_id))
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.group_start_keys.key_unavailable.degraded", exc_info=True
        )


async def release(found: Claim) -> None:
    """The start failed: a retry with the key may try again."""
    if found.suffix is None or not found.held:
        return
    try:
        await _keys().delete_if_value(found.suffix, _PENDING)
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.group_start_keys.key_unavailable.degraded", exc_info=True
        )
