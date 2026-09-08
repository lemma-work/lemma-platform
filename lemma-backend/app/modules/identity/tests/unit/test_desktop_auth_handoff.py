from __future__ import annotations

import base64
import hashlib
from uuid import uuid4

import pytest

from app.modules.identity.services.desktop_auth_handoff import (
    DesktopAuthCompletionConflict,
    DesktopAuthHandoffStore,
    DesktopAuthRateLimitExceeded,
    DesktopAuthRequestNotFound,
    DesktopAuthRequestPending,
    DesktopAuthVerifierRejected,
    challenge_for_verifier,
)


def test_challenge_for_verifier_is_unpadded_sha256_base64url():
    verifier = "desktop-verifier-with-enough-entropy-0123456789"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        .decode("ascii")
        .rstrip("=")
    )

    assert challenge_for_verifier(verifier) == expected
    assert "=" not in expected


@pytest.mark.asyncio
async def test_completion_is_idempotent_for_same_user_and_rejects_replacement():
    redis = _FakeRedis()
    store = _store(redis, create_limit=5)
    verifier = "v" * 43
    request = await store.create(
        challenge_for_verifier(verifier),
        client_key="127.0.0.1",
    )
    first_user = uuid4()

    await store.complete(request.request_id, first_user)
    await store.complete(request.request_id, first_user)
    with pytest.raises(DesktopAuthCompletionConflict):
        await store.complete(request.request_id, uuid4())

    assert await store.consume(request.request_id, verifier) == first_user
    with pytest.raises(DesktopAuthRequestNotFound):
        await store.consume(request.request_id, verifier)


@pytest.mark.asyncio
async def test_create_rate_limit_bounds_redis_handoffs():
    redis = _FakeRedis()
    store = _store(redis, create_limit=2, create_window_seconds=45)

    await store.create("a" * 43, client_key="client-a")
    await store.create("b" * 43, client_key="client-a")
    with pytest.raises(DesktopAuthRateLimitExceeded) as exc:
        await store.create("c" * 43, client_key="client-a")

    assert exc.value.retry_after_seconds == 45
    assert len(redis.hashes) == 2
    await store.create("d" * 43, client_key="client-b")


@pytest.mark.asyncio
async def test_a_wrong_verifier_is_refused_without_burning_the_request():
    """The half of the exchange that proves the caller is the app that began it.

    Only the desktop app holds the verifier; the browser carries the challenge,
    which is its hash. Anyone who obtains the request id but not the verifier
    must get nothing -- and must not be able to destroy the login either, or
    knowing an id would be enough to lock the real app out of its own handoff.
    """
    redis = _FakeRedis()
    store = _store(redis, create_limit=5)
    verifier = "v" * 43
    request = await store.create(challenge_for_verifier(verifier), client_key="127.0.0.1")
    user = uuid4()
    await store.complete(request.request_id, user)

    with pytest.raises(DesktopAuthVerifierRejected):
        await store.consume(request.request_id, "w" * 43)

    # Still there, and still the right user's.
    assert await store.consume(request.request_id, verifier) == user


@pytest.mark.asyncio
async def test_consuming_before_the_browser_finishes_reveals_nothing():
    """Pending is its own answer, and it is not a user id.

    A request that has been created but not completed must not resolve to
    anybody. Returning the empty or default user here is how a handoff becomes
    a way to be signed in as someone else.
    """
    redis = _FakeRedis()
    store = _store(redis, create_limit=5)
    verifier = "v" * 43
    request = await store.create(challenge_for_verifier(verifier), client_key="127.0.0.1")

    with pytest.raises(DesktopAuthRequestPending):
        await store.consume(request.request_id, verifier)


@pytest.mark.asyncio
async def test_a_consumed_handoff_cannot_be_replayed_by_anybody():
    """One exchange, one session.

    The verifier does not expire on its own and a desktop log, a crash report
    or a shared screen can carry it. What stops it being reused is that
    consuming destroys the record -- so a replay has to be indistinguishable
    from a request that never existed.
    """
    redis = _FakeRedis()
    store = _store(redis, create_limit=5)
    verifier = "v" * 43
    request = await store.create(challenge_for_verifier(verifier), client_key="127.0.0.1")
    await store.complete(request.request_id, uuid4())
    await store.consume(request.request_id, verifier)

    with pytest.raises(DesktopAuthRequestNotFound):
        await store.consume(request.request_id, verifier)
    # And the record itself is gone, not merely marked.
    assert redis.hashes == {}


@pytest.mark.asyncio
async def test_an_expired_handoff_is_indistinguishable_from_one_that_never_existed():
    """The window is the whole of the protection once an id is out.

    Nothing rate-limits `consume`, so the request's expiry is what bounds how
    long a leaked id is worth anything. Both verbs have to treat an expired
    record the way they treat an unknown one -- including `complete`, which the
    browser reaches after the user has signed in and which must not resurrect a
    request whose window has closed.
    """
    redis = _FakeRedis()
    store = _store(redis, create_limit=5, ttl_seconds=45)
    verifier = "v" * 43
    request = await store.create(challenge_for_verifier(verifier), client_key="127.0.0.1")

    key = next(iter(redis.hashes))
    assert redis.expires[key] == 45, "the record must not outlive the configured window"

    redis.expire_now(key)

    with pytest.raises(DesktopAuthRequestNotFound):
        await store.complete(request.request_id, uuid4())
    with pytest.raises(DesktopAuthRequestNotFound):
        await store.consume(request.request_id, verifier)


def _store(redis, **kwargs) -> DesktopAuthHandoffStore:
    store = DesktopAuthHandoffStore(**kwargs)
    store._redis = redis
    return store


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.actions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def hset(self, key, *, mapping):
        self.actions.append(("hset", key, mapping))

    def expire(self, key, ttl):
        self.actions.append(("expire", key, ttl))

    async def execute(self):
        for action, key, value in self.actions:
            if action == "hset":
                self.redis.hashes[key] = dict(value)
            else:
                self.redis.expires[key] = value


class _FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.counts = {}
        self.expires = {}

    def expire_now(self, key):
        """What Redis does when the TTL runs out: the key is simply gone."""
        self.hashes.pop(key, None)
        self.expires.pop(key, None)

    def pipeline(self, *, transaction):
        assert transaction is True
        return _FakePipeline(self)

    async def eval(self, script, _key_count, key, *args):
        if "INCR" in script:
            count = self.counts.get(key, 0) + 1
            self.counts[key] = count
            window = int(args[0])
            self.expires.setdefault(key, window)
            return [count, self.expires[key]]

        record = self.hashes.get(key)
        if record is None:
            return ["missing"]

        if "completed_user_id" in script:
            user_id = str(args[0])
            if record["status"] == "complete":
                return ["complete" if record.get("user_id") == user_id else "conflict"]
            if record["status"] != "pending":
                return ["conflict"]
            record.update(status="complete", user_id=user_id)
            return ["complete"]

        challenge = str(args[0])
        if record["challenge"] != challenge:
            return ["forbidden"]
        if record["status"] != "complete":
            return ["pending"]
        user_id = record["user_id"]
        del self.hashes[key]
        return ["complete", user_id]
