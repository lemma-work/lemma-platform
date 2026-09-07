from __future__ import annotations

from uuid import UUID

from app.core.auth_state_cache import invalidate_auth_state
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.identity.domain.ports import UserCachePort
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.config import identity_settings


class RedisUserCache(UserCachePort):
    def __init__(self, redis_url: str, ttl_seconds: int):
        self._redis_url = redis_url
        self._cache = RedisJsonCache[UserEntity](
            redis_url=redis_url,
            key_prefix="identity:user",
            ttl_seconds=ttl_seconds,
        )

    async def get(self, user_id: UUID) -> UserEntity | None:
        payload = await self._cache.get_raw(str(user_id))
        if not payload:
            return None
        return UserEntity.model_validate_json(payload)

    async def set(self, user: UserEntity) -> None:
        await self._cache.set_raw(str(user.id), user.model_dump_json())

    async def invalidate(self, user_id: UUID) -> None:
        await self._cache.delete(str(user_id))
        # The account standing that `verify_auth` caches is derived from the
        # same row, and every place that changes it — deactivation on a hard
        # bounce, email verification, deletion — already invalidates here.
        # Dropping both together is what makes that true by construction
        # instead of by remembering to add a second call each time.
        await invalidate_auth_state(user_id)

    async def close(self) -> None:
        await self._cache.close()


_user_cache: RedisUserCache | None = None


def get_user_cache() -> RedisUserCache:
    global _user_cache
    if _user_cache is None or _user_cache._redis_url != settings.redis_url:
        _user_cache = RedisUserCache(
            redis_url=settings.redis_url,
            ttl_seconds=identity_settings.user_cache_ttl_seconds,
        )
    return _user_cache


async def close_user_cache() -> None:
    global _user_cache
    if _user_cache is None:
        return
    await _user_cache.close()
    _user_cache = None
