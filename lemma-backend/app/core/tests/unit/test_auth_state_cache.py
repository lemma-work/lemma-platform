"""Account standing is cached, but never in a way that outlives a revocation.

The cache exists to keep a per-request database read off every authenticated
endpoint. What matters is the other direction: a deactivated account must stop
being served promptly, so these cover the invalidation and the fail-open-to-the-
database behaviour as carefully as the hit.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core import auth_state_cache
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache
from app.core.auth_state_cache import (
    AccountStanding,
    get_account_standing,
    invalidate_auth_state,
    set_account_standing,
)

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Stands in for Redis, with a switch for making it unavailable."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.fail = False

    async def get_raw(self, suffix: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis is down")
        return self.store.get(suffix)

    async def set_raw(self, suffix: str, payload: str) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.store[suffix] = payload

    async def delete(self, suffix: str) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.store.pop(suffix, None)


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """A real ResilientJsonCache over a fake Redis.

    The wrapper is what turns an outage into a miss, so stubbing it out would
    leave the behaviour these tests exist for untested.
    """
    redis = _FakeRedis()
    cache = ResilientJsonCache(
        name="auth_state_cache_test",
        key_prefix="test",
        redis_url="redis://localhost:6379/0",
        ttl_seconds=30,
    )
    cache._cache = redis  # type: ignore[assignment]
    monkeypatch.setattr(auth_state_cache, "_get_cache", lambda: cache)
    return redis


async def test_standing_round_trips(fake_cache: _FakeRedis) -> None:
    user_id = uuid4()
    await set_account_standing(
        user_id, AccountStanding(is_active=True, is_verified=True, is_deleted=False)
    )

    assert await get_account_standing(user_id) == AccountStanding(
        is_active=True, is_verified=True, is_deleted=False
    )


async def test_every_flag_survives_the_round_trip(fake_cache: _FakeRedis) -> None:
    """A flag that decoded to the wrong value would admit a blocked account."""
    user_id = uuid4()
    await set_account_standing(
        user_id, AccountStanding(is_active=False, is_verified=False, is_deleted=True)
    )

    standing = await get_account_standing(user_id)
    assert standing == AccountStanding(
        is_active=False, is_verified=False, is_deleted=True
    )


async def test_invalidation_drops_the_entry(fake_cache: _FakeRedis) -> None:
    """Deactivation must take effect on the next request, not at the TTL."""
    user_id = uuid4()
    await set_account_standing(
        user_id, AccountStanding(is_active=True, is_verified=True, is_deleted=False)
    )

    await invalidate_auth_state(user_id)

    assert await get_account_standing(user_id) is None


async def test_invalidation_is_scoped_to_one_account(fake_cache: _FakeRedis) -> None:
    kept, dropped = uuid4(), uuid4()
    standing = AccountStanding(is_active=True, is_verified=True, is_deleted=False)
    await set_account_standing(kept, standing)
    await set_account_standing(dropped, standing)

    await invalidate_auth_state(dropped)

    assert await get_account_standing(kept) == standing
    assert await get_account_standing(dropped) is None


async def test_redis_outage_reads_as_a_miss(fake_cache: _FakeRedis) -> None:
    """A cache failure must send the caller to the database, not reject them."""
    user_id = uuid4()
    await set_account_standing(
        user_id, AccountStanding(is_active=True, is_verified=True, is_deleted=False)
    )
    fake_cache.fail = True

    assert await get_account_standing(user_id) is None


async def test_a_corrupt_payload_reads_as_a_miss(fake_cache: _FakeRedis) -> None:
    """Never guess at a payload this decides access on."""
    user_id = uuid4()
    fake_cache.store[str(user_id)] = "{not json"

    assert await get_account_standing(user_id) is None


async def test_disabled_cache_never_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL 0 means the database is consulted on every request, by design."""
    monkeypatch.setattr(auth_state_cache, "_get_cache", lambda: None)
    user_id = uuid4()

    await set_account_standing(
        user_id, AccountStanding(is_active=True, is_verified=True, is_deleted=False)
    )

    assert await get_account_standing(user_id) is None
