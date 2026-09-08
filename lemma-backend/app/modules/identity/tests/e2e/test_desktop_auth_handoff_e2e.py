"""The handoff's real Lua, against a real Redis.

The rules that make a desktop login safe live in two Lua scripts: one that
completes a request exactly once for exactly one user, and one that exchanges
it exactly once for the caller holding the verifier. Everything else is
argument passing.

The unit tests drive a hand-written Python stand-in for those scripts. That
proves the Python around them and certifies nothing about the scripts
themselves -- a stand-in agrees with whatever it was written to agree with,
and Redis is the half that decides. These run the shipped scripts.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.infrastructure.redis.client import get_redis
from app.modules.identity.services.desktop_auth_handoff import (
    DesktopAuthCompletionConflict,
    DesktopAuthHandoffStore,
    DesktopAuthRequestNotFound,
    DesktopAuthRequestPending,
    DesktopAuthVerifierRejected,
    challenge_for_verifier,
)

pytestmark = pytest.mark.e2e

VERIFIER = "desktop-verifier-with-enough-entropy-0123456789"


@pytest.fixture
def store(test_redis_url):
    store = DesktopAuthHandoffStore()
    store._redis = get_redis(url=test_redis_url)
    return store


@pytest.mark.asyncio
async def test_a_handoff_completes_once_and_exchanges_once(store):
    request = await store.create(challenge_for_verifier(VERIFIER), client_key="e2e")
    user = uuid4()

    await store.complete(request.request_id, user)
    # Idempotent for the same user: the browser can and does retry.
    await store.complete(request.request_id, user)
    with pytest.raises(DesktopAuthCompletionConflict):
        await store.complete(request.request_id, uuid4())

    assert await store.consume(request.request_id, VERIFIER) == user
    with pytest.raises(DesktopAuthRequestNotFound):
        await store.consume(request.request_id, VERIFIER)


@pytest.mark.asyncio
async def test_the_verifier_is_what_the_exchange_actually_checks(store):
    request = await store.create(challenge_for_verifier(VERIFIER), client_key="e2e")
    user = uuid4()
    await store.complete(request.request_id, user)

    with pytest.raises(DesktopAuthVerifierRejected):
        await store.consume(request.request_id, "a-different-verifier-entirely-01234")

    # A wrong guess must not destroy the login either: knowing an id would
    # otherwise be enough to lock the real app out of its own handoff.
    assert await store.consume(request.request_id, VERIFIER) == user


@pytest.mark.asyncio
async def test_an_unfinished_handoff_resolves_to_nobody(store):
    request = await store.create(challenge_for_verifier(VERIFIER), client_key="e2e")

    with pytest.raises(DesktopAuthRequestPending):
        await store.consume(request.request_id, VERIFIER)


@pytest.mark.asyncio
async def test_an_unknown_request_is_not_distinguishable_from_an_expired_one(store):
    with pytest.raises(DesktopAuthRequestNotFound):
        await store.complete("never-issued-request-id", uuid4())
    with pytest.raises(DesktopAuthRequestNotFound):
        await store.consume("never-issued-request-id", VERIFIER)


@pytest.mark.asyncio
async def test_the_record_carries_the_expiry_that_bounds_a_leaked_id(store):
    """Nothing rate-limits `consume`, so the window is what bounds the damage."""
    store._ttl_seconds = 45
    request = await store.create(challenge_for_verifier(VERIFIER), client_key="e2e")

    ttl = await store._redis.ttl(store._key(request.request_id))
    assert 0 < ttl <= 45
