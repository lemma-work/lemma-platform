"""At most one retry of one failed document per cooldown.

A retry costs the pod a full extraction and embedding, and a document that
failed permanently usually fails again -- so a client retrying in a loop, or a
person clicking repeatedly, must not turn into a processing loop of its own.
Redis holds the slot because every API replica must agree on it; Redis being
unreachable lets the retry through, since the work item's own budgets still
bound what one retry can cost.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings

logger = get_logger(__name__)

_slots: RedisJsonCache[str] | None = None


def _cache() -> RedisJsonCache[str]:
    global _slots
    if _slots is None:
        _slots = RedisJsonCache(settings.redis_url, "datastore:retry-processing", 60)
    return _slots


async def claim_retry_slot(file_id: UUID) -> bool:
    """True when this file may be retried now; the slot lasts one cooldown."""
    cooldown = datastore_settings.document_processing_retry_cooldown_seconds
    if cooldown <= 0:
        return True
    try:
        return await _cache().set_raw_if_absent(str(file_id), "1", ttl_seconds=cooldown)
    except RedisError, OSError:
        logger.warning(
            "datastore.retry_gate.unavailable.degraded",
            file_id=str(file_id),
            exc_info=True,
        )
        return True
