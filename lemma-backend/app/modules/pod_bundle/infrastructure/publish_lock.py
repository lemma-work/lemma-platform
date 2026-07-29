"""Repository-scoped distributed lock for GitHub publishing."""

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

    @staticmethod
    def _key(account_id: UUID, repo_name: str) -> str:
        return f"{account_id}:{repo_name.casefold()}"

    async def acquire(
        self,
        *,
        account_id: UUID,
        repo_name: str,
        owner: UUID,
    ) -> bool:
        return await self._cache.set_raw_if_absent(
            self._key(account_id, repo_name),
            str(owner),
        )

    async def release(
        self,
        *,
        account_id: UUID,
        repo_name: str,
        owner: UUID,
    ) -> None:
        try:
            await self._cache.delete_if_value(
                self._key(account_id, repo_name),
                str(owner),
            )
        except RedisError:
            logger.debug(
                "pod_bundle.publish_lock.release.diagnostic",
                account_id=str(account_id),
                repo_name=repo_name,
            )


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
        if isinstance(state, PublishState) and state.account_id is not None:
            await lock.release(
                account_id=state.account_id,
                repo_name=state.repo_name,
                owner=state.publish_id,
            )
