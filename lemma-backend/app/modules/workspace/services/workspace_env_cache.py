"""Cache workspace environment variables for tool-driven command execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import settings
from app.core.crypto import get_secret_cipher
from app.core.crypto.envelope import is_encrypted_dict
from app.core.crypto.ports import SecretCipher

_DEFAULT_TTL_SECONDS = 5 * 60


class WorkspaceEnvCachePort(Protocol):
    async def get(self, key: str) -> dict[str, str] | None: ...

    async def set(self, key: str, env_vars: dict[str, str], ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def close(self) -> None: ...


class RedisWorkspaceEnvCache(WorkspaceEnvCachePort):
    def __init__(
        self,
        *,
        redis_url: str | None = None,
        key_prefix: str = "workspace:env:v2",
        cipher: SecretCipher | None = None,
    ):
        self._redis = Redis.from_url(redis_url or settings.redis_url, decode_responses=True)
        self._key_prefix = key_prefix
        self._cipher = cipher or get_secret_cipher()

    def _cache_key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    async def get(self, key: str) -> dict[str, str] | None:
        raw = await self._redis.get(self._cache_key(key))
        if not raw:
            return None
        encrypted = json.loads(raw)
        if not is_encrypted_dict(encrypted):
            # Never continue using legacy plaintext token caches. A fresh,
            # encrypted value is written by the caller after this cache miss.
            await self.delete(key)
            return None
        payload = await self._cipher.decrypt_json_async(encrypted)
        if not isinstance(payload, dict):
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
        payload: dict = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "env_vars": env_vars,
        }
        encrypted = await self._cipher.encrypt_json_async(payload)
        if encrypted is None:
            raise RuntimeError("Workspace environment encryption returned no payload")
        await self._redis.set(
            self._cache_key(key),
            json.dumps(encrypted),
            ex=max(1, ttl_seconds),
        )

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._cache_key(key))

    async def delete_matching(self, pattern: str) -> None:
        keys: list[str] = []
        async for key in self._redis.scan_iter(match=self._cache_key(pattern), count=100):
            keys.append(str(key))
            if len(keys) >= 100:
                await self._redis.delete(*keys)
                keys.clear()
        if keys:
            await self._redis.delete(*keys)

    async def close(self) -> None:
        await self._redis.aclose()


def get_default_workspace_env_ttl_seconds() -> int:
    return _DEFAULT_TTL_SECONDS
