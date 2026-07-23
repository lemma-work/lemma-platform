from __future__ import annotations

import asyncio
from uuid import uuid7

from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenCache,
    FunctionSessionTokenKey,
)


def _key(*, revision_hash: str | None = None) -> FunctionSessionTokenKey:
    return FunctionSessionTokenKey(
        user_id=uuid7(),
        pod_id=uuid7(),
        function_id=uuid7(),
        revision_hash=revision_hash or f"sha256:{'a' * 64}",
        workload_name="read_records",
        scope=(),
        delegated_tokens_enabled=True,
    )


async def test_concurrent_cache_miss_mints_one_function_session() -> None:
    cache = FunctionSessionTokenCache(ttl_seconds=300)
    key = _key()
    calls = 0
    session_ids: list[str] = []

    async def mint(**kwargs) -> str:
        nonlocal calls
        calls += 1
        session_ids.append(kwargs["session_id"])
        await asyncio.sleep(0.01)
        return "cached-function-token"

    results = await asyncio.gather(
        *(cache.get(key, minter=mint) for _ in range(20))
    )

    assert results == ["cached-function-token"] * 20
    assert calls == 1
    assert session_ids == [key.session_id]


async def test_cache_expiry_and_revision_hash_mint_new_sessions() -> None:
    now = 100.0
    cache = FunctionSessionTokenCache(ttl_seconds=300, clock=lambda: now)
    key = _key()
    calls = 0

    async def mint(**_kwargs) -> str:
        nonlocal calls
        calls += 1
        return f"token-{calls}"

    assert await cache.get(key, minter=mint) == "token-1"
    assert await cache.get(key, minter=mint) == "token-1"

    changed_revision = FunctionSessionTokenKey(
        user_id=key.user_id,
        pod_id=key.pod_id,
        function_id=key.function_id,
        revision_hash=f"sha256:{'b' * 64}",
        workload_name=key.workload_name,
        scope=key.scope,
        delegated_tokens_enabled=key.delegated_tokens_enabled,
    )
    assert await cache.get(changed_revision, minter=mint) == "token-2"

    now += 301
    assert await cache.get(key, minter=mint) == "token-3"
    assert calls == 3
