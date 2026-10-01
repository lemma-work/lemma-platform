from __future__ import annotations

import json

import pytest

from app.modules.test_support.vault_fake import StaticSealingKeys
from app.modules.workspace.services.workspace_env_cache import RedisWorkspaceEnvCache


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.deleted: list[str] = []

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int):
        self.values[key] = value
        self.expiries[key] = ex

    async def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
            self.deleted.append(key)

    async def aclose(self):
        return None


def _cache(redis: _FakeRedis) -> RedisWorkspaceEnvCache:
    cache = RedisWorkspaceEnvCache(keyring=StaticSealingKeys())
    cache._redis = redis  # type: ignore[assignment]
    return cache


@pytest.mark.asyncio
async def test_workspace_cache_never_stores_delegated_token_in_plaintext() -> None:
    redis = _FakeRedis()
    cache = _cache(redis)

    await cache.set("isolated-session", {"LEMMA_TOKEN": "CANARY-TOKEN"}, 300)

    stored = redis.values["workspace:env:v3:isolated-session"]
    assert "CANARY-TOKEN" not in stored
    assert stored.startswith("lvs1:")
    assert await cache.get("isolated-session") == {"LEMMA_TOKEN": "CANARY-TOKEN"}
    assert redis.expiries["workspace:env:v3:isolated-session"] == 300


@pytest.mark.asyncio
async def test_workspace_cache_deletes_legacy_plaintext_value() -> None:
    redis = _FakeRedis()
    cache = _cache(redis)
    key = "workspace:env:v3:legacy"
    redis.values[key] = json.dumps({"env_vars": {"LEMMA_TOKEN": "plaintext"}})

    assert await cache.get("legacy") is None
    assert key in redis.deleted


@pytest.mark.asyncio
async def test_a_value_moved_to_another_key_does_not_open() -> None:
    """The seal is bound to its Redis key; another session's entry is a miss."""
    redis = _FakeRedis()
    cache = _cache(redis)
    await cache.set("session-a", {"LEMMA_TOKEN": "token-a"}, 300)
    redis.values["workspace:env:v3:session-b"] = redis.values[
        "workspace:env:v3:session-a"
    ]

    assert await cache.get("session-b") is None
    assert "workspace:env:v3:session-b" not in redis.values
    assert await cache.get("session-a") == {"LEMMA_TOKEN": "token-a"}
