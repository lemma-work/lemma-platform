from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid7

from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
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

    async def mint(**kwargs) -> FunctionSessionToken:
        nonlocal calls
        calls += 1
        session_ids.append(kwargs["session_id"])
        await asyncio.sleep(0.01)
        return FunctionSessionToken(
            value="cached-function-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    results = await asyncio.gather(*(cache.get(key, minter=mint) for _ in range(20)))

    assert [result.value for result in results] == ["cached-function-token"] * 20
    assert calls == 1
    assert session_ids == [key.session_id]


async def test_cache_expiry_and_revision_hash_mint_new_sessions() -> None:
    monotonic_now = 100.0
    wall_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cache = FunctionSessionTokenCache(
        ttl_seconds=300,
        clock=lambda: monotonic_now,
        wall_clock=lambda: wall_now,
    )
    key = _key()
    calls = 0

    async def mint(**_kwargs) -> FunctionSessionToken:
        nonlocal calls
        calls += 1
        return FunctionSessionToken(
            value=f"token-{calls}",
            expires_at=wall_now + timedelta(hours=1),
        )

    assert (await cache.get(key, minter=mint)).value == "token-1"
    assert (await cache.get(key, minter=mint)).value == "token-1"

    changed_revision = FunctionSessionTokenKey(
        user_id=key.user_id,
        pod_id=key.pod_id,
        function_id=key.function_id,
        revision_hash=f"sha256:{'b' * 64}",
        workload_name=key.workload_name,
        scope=key.scope,
        delegated_tokens_enabled=key.delegated_tokens_enabled,
    )
    assert (await cache.get(changed_revision, minter=mint)).value == "token-2"

    monotonic_now += 301
    assert (await cache.get(key, minter=mint)).value == "token-3"
    assert calls == 3


async def test_cache_mints_fresh_token_for_required_validity_window() -> None:
    wall_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cache = FunctionSessionTokenCache(wall_clock=lambda: wall_now)
    calls = 0
    key = _key()

    async def mint(**_kwargs) -> FunctionSessionToken:
        nonlocal calls
        calls += 1
        lifetime = 10 if calls == 1 else 120
        return FunctionSessionToken(
            value=f"token-{calls}",
            expires_at=wall_now + timedelta(seconds=lifetime),
        )

    assert (await cache.get(key, minter=mint)).value == "token-1"
    assert (
        await cache.get(
            key,
            minter=mint,
            min_validity_until=wall_now + timedelta(seconds=60),
        )
    ).value == "token-2"
