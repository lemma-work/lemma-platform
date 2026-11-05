"""(profile, model) pairs a provider recently said it does not serve.

Kept in Redis, not in the process: the worker that saw the provider's 404 is
not the API process that starts the next turn, and every replica must refuse
it alike.
"""

from __future__ import annotations

from functools import lru_cache

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: How long a provider's "no such model" is believed. Long enough that a burst
#: of turns on a dead model is refused without asking again; short enough that
#: a model the provider restores, or a fixed base URL, is back without anyone
#: having to clear anything.
MODEL_UNAVAILABLE_TTL_SECONDS = 600


class UnavailableModels:
    """(profile, model) pairs a provider recently said it does not serve."""

    def __init__(self, cache: RedisJsonCache[dict[str, bool]]) -> None:
        self._cache = cache

    async def mark(self, *, profile_id: str, model_name: str) -> None:
        try:
            await self._cache.set_json(_key(profile_id, model_name), {"404": True})
        except RedisError, OSError, TimeoutError:  # a lost hint only costs one more 404
            logger.warning(
                "agent.runtime_availability.mark_failed.degraded",
                profile_id=profile_id,
                exc_info=True,
            )

    async def is_marked(self, *, profile_id: str, model_name: str) -> bool:
        try:
            return await self._cache.get_json(_key(profile_id, model_name)) is not None
        except RedisError, OSError, TimeoutError:  # the provider is still the authority
            logger.warning(
                "agent.runtime_availability.lookup_failed.degraded",
                profile_id=profile_id,
                exc_info=True,
            )
            return False


def _key(profile_id: str, model_name: str) -> str:
    return f"{profile_id}:{model_name}"


@lru_cache(maxsize=1)
def unavailable_models() -> UnavailableModels:
    return UnavailableModels(
        RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="agent:model-unavailable",
            ttl_seconds=MODEL_UNAVAILABLE_TTL_SECONDS,
        )
    )


async def remember_provider_refusal(
    runtime_profile: RuntimeSnapshot | None,
    *,
    status_code: int,
    store: UnavailableModels | None = None,
) -> None:
    """Note a provider's 404 for the run's (profile, model)."""
    if status_code != 404 or runtime_profile is None:
        return
    if runtime_profile.model_name is not None:
        await (store or unavailable_models()).mark(
            profile_id=runtime_profile.profile_id,
            model_name=runtime_profile.model_name,
        )
