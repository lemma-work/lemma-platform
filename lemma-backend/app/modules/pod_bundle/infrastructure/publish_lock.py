"""Repository-scoped distributed lock for GitHub publishing.

Keyed on the GitHub account a publish runs as and the repository name, because
that pair is what two publishes would collide on at GitHub. The account is the
one the pod's `github` connector resolved to when the publish started; the job
keeps only the opaque key, to release it.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.state import (
    ExportState,
    ImportState,
    PublishState,
)

logger = get_logger(__name__)


class PublishConcurrencyLock:
    def __init__(self, cache: RedisJsonCache | None = None):
        self._cache = cache or RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="pod-bundle:publish-lock",
            ttl_seconds=pod_bundle_settings.pod_bundle_publish_lock_ttl_seconds,
        )

    async def acquire(self, *, key: str, owner: UUID) -> bool:
        return await self._cache.set_raw_if_absent(key, str(owner))

    async def release(self, *, key: str, owner: UUID) -> None:
        try:
            await self._cache.delete_if_value(key, str(owner))
        except RedisError:
            logger.debug("pod_bundle.publish_lock.release.diagnostic", key=key)


def publish_lock_key(account_id: UUID, repo_name: str) -> str:
    return f"{account_id}:{repo_name.casefold()}"


_publish_lock: PublishConcurrencyLock | None = None


def get_publish_concurrency_lock() -> PublishConcurrencyLock:
    global _publish_lock
    if _publish_lock is None:
        _publish_lock = PublishConcurrencyLock()
    return _publish_lock


async def release_recovered_publish_locks(
    states: list[ImportState | ExportState | PublishState],
) -> None:
    lock = get_publish_concurrency_lock()
    for state in states:
        if isinstance(state, PublishState) and state.publish_lock_key is not None:
            await lock.release(key=state.publish_lock_key, owner=state.publish_id)
