"""Cache workspace environment variables for tool-driven command execution.

The variables include delegated Lemma tokens, so an entry is sealed (see the
vault's sealer) and bound to its full Redis key: never plaintext, and a value
copied under another key does not open.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


from app.core.infrastructure.redis.client import get_redis

from app.core.config import settings
from app.modules.vault.contracts import (
    SealedValueInvalid,
    SealingKeys,
    open_json,
    seal_value,
)

_DEFAULT_TTL_SECONDS = 5 * 60
ENV_CACHE_PURPOSE = "workspace.env_cache"


class WorkspaceEnvCachePort(Protocol):
    async def get(self, key: str) -> dict[str, str] | None: ...

    async def set(
        self, key: str, env_vars: dict[str, str], ttl_seconds: int
    ) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def close(self) -> None: ...


class RedisWorkspaceEnvCache(WorkspaceEnvCachePort):
    def __init__(
        self,
        *,
        redis_url: str | None = None,
        # v3 holds sealed values. v2 entries are never read; they expire.
        key_prefix: str = "workspace:env:v3",
        keyring: SealingKeys | None = None,
    ):
        self._redis = get_redis(url=redis_url or settings.redis_url)
        self._key_prefix = key_prefix
        self._keyring = keyring

    def _cache_key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    async def get(self, key: str) -> dict[str, str] | None:
        cache_key = self._cache_key(key)
        raw = await self._redis.get(cache_key)
        if not raw:
            return None
        sealed = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            payload = await open_json(
                sealed,
                purpose=ENV_CACHE_PURPOSE,
                bindings=[cache_key],
                keyring=self._keyring,
            )
        except SealedValueInvalid:
            # Plaintext, or sealed under another key: never used. The caller
            # writes a fresh sealed value after this miss.
            await self.delete(key)
            return None
        env_vars = payload.get("env_vars")
        if not isinstance(env_vars, dict):
            return None
        return {
            str(k): str(v)
            for k, v in env_vars.items()
            if isinstance(k, str) and isinstance(v, str)
        }

    async def set(self, key: str, env_vars: dict[str, str], ttl_seconds: int) -> None:
        cache_key = self._cache_key(key)
        sealed = await seal_value(
            {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "env_vars": dict(env_vars),
            },
            purpose=ENV_CACHE_PURPOSE,
            bindings=[cache_key],
            keyring=self._keyring,
        )
        await self._redis.set(cache_key, sealed, ex=max(1, ttl_seconds))

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._cache_key(key))

    async def delete_matching(self, pattern: str) -> None:
        keys: list[str] = []
        async for key in self._redis.scan_iter(
            match=self._cache_key(pattern), count=100
        ):
            keys.append(str(key))
            if len(keys) >= 100:
                await self._redis.delete(*keys)
                keys.clear()
        if keys:
            await self._redis.delete(*keys)

    async def close(self) -> None:
        # The client is shared process-wide; closing it here would break
        # every other component still using the same pool. Disposal is
        # close_redis_clients()'s job at lifespan shutdown.
        self._redis = None


def get_default_workspace_env_ttl_seconds() -> int:
    return _DEFAULT_TTL_SECONDS
