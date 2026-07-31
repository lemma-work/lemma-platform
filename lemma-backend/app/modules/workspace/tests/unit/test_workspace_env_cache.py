from __future__ import annotations

import json

import pytest

from app.core.crypto.envelope import ALG_FERNET, make_v2
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


class _FakeCipher:
    def __init__(self) -> None:
        self.plaintext: dict | None = None

    async def encrypt_json_async(self, value):
        self.plaintext = value
        return make_v2(kid="test", alg=ALG_FERNET, ciphertext=b"opaque")

    async def decrypt_json_async(self, value):
        assert value["_encrypted"] == "lemma-secret-v2"
        return self.plaintext


@pytest.mark.asyncio
async def test_workspace_cache_never_stores_delegated_token_in_plaintext() -> None:
    redis = _FakeRedis()
    cipher = _FakeCipher()
    cache = RedisWorkspaceEnvCache(cipher=cipher)
    cache._redis = redis  # type: ignore[assignment]

    await cache.set("isolated-session", {"LEMMA_TOKEN": "CANARY-TOKEN"}, 300)

    stored = redis.values["workspace:env:v2:isolated-session"]
    assert "CANARY-TOKEN" not in stored
    assert json.loads(stored)["_encrypted"] == "lemma-secret-v2"
    assert await cache.get("isolated-session") == {"LEMMA_TOKEN": "CANARY-TOKEN"}
    assert redis.expiries["workspace:env:v2:isolated-session"] == 300


@pytest.mark.asyncio
async def test_workspace_cache_deletes_legacy_plaintext_value() -> None:
    redis = _FakeRedis()
    cache = RedisWorkspaceEnvCache(cipher=_FakeCipher())
    cache._redis = redis  # type: ignore[assignment]
    key = "workspace:env:v2:legacy"
    redis.values[key] = json.dumps({"env_vars": {"LEMMA_TOKEN": "plaintext"}})

    assert await cache.get("legacy") is None
    assert key in redis.deleted
