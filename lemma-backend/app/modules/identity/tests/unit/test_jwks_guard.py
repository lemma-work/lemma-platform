"""The JWKS guard has to stop the amplification without breaking rotation.

The attack it exists for: `kid` is read from a token header *before* the
signature is checked, and SuperTokens has no negative cache, so forged tokens
carrying random `kid` values each cost one synchronous HTTP round trip on the
event loop, under a lock that excludes every other verification.
"""

from __future__ import annotations

import pytest

from app.modules.identity.infrastructure.supertokens_auth import jwks_guard


@pytest.fixture(autouse=True)
def _restore_guard():
    yield
    jwks_guard.reset_jwks_guard_for_test()


@pytest.fixture
def fetches(monkeypatch):
    """Install the guard over a fake upstream, counting network fetches."""
    calls: list[str | None] = []

    def _fake(config, kid=None):
        calls.append(kid)
        if kid in (None, "known"):
            return ["key"]
        raise Exception("No matching JWKS found")

    jwks_guard.install_jwks_guard()
    monkeypatch.setattr(jwks_guard, "_original_get_latest_keys", _fake)
    return calls


def test_an_unknown_kid_is_fetched_once_not_once_per_request(fetches):
    """The whole point: N forged requests must not cost N round trips."""
    for _ in range(50):
        with pytest.raises(Exception):
            jwks_guard._guarded_get_latest_keys(object(), "forged")

    assert fetches == ["forged"]


def test_distinct_forged_kids_each_cost_at_most_one_fetch(fetches):
    for index in range(10):
        with pytest.raises(Exception):
            jwks_guard._guarded_get_latest_keys(object(), f"forged-{index}")
    for index in range(10):
        with pytest.raises(Exception):
            jwks_guard._guarded_get_latest_keys(object(), f"forged-{index}")

    assert len(fetches) == 10


def test_a_known_kid_is_never_blocked(fetches):
    assert jwks_guard._guarded_get_latest_keys(object(), "known") == ["key"]
    assert jwks_guard._guarded_get_latest_keys(object(), "known") == ["key"]
    assert fetches == ["known", "known"]


def test_the_negative_cache_expires_so_rotation_recovers(fetches, monkeypatch):
    """A key published after we rejected its id must become usable."""
    clock = {"t": 0.0}
    monkeypatch.setattr(jwks_guard.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(jwks_guard.settings, "auth_jwks_unknown_kid_ttl_seconds", 60.0)

    with pytest.raises(Exception):
        jwks_guard._guarded_get_latest_keys(object(), "rotating")
    with pytest.raises(Exception):
        jwks_guard._guarded_get_latest_keys(object(), "rotating")
    assert len(fetches) == 1

    clock["t"] = 61.0
    with pytest.raises(Exception):
        jwks_guard._guarded_get_latest_keys(object(), "rotating")
    assert len(fetches) == 2


def test_a_successful_fetch_clears_the_negative_cache(fetches):
    """A rotation that introduces new keys must not stay shadowed."""
    with pytest.raises(Exception):
        jwks_guard._guarded_get_latest_keys(object(), "later-valid")
    assert jwks_guard._unknown_kids

    jwks_guard._guarded_get_latest_keys(object(), "known")

    assert jwks_guard._unknown_kids == {}


def test_the_negative_cache_is_bounded(fetches, monkeypatch):
    """The sender picks the ids, so an unbounded map just moves the damage."""
    monkeypatch.setattr(jwks_guard.settings, "auth_jwks_unknown_kid_cache_size", 8)

    for index in range(40):
        with pytest.raises(Exception):
            jwks_guard._guarded_get_latest_keys(object(), f"k{index}")

    assert len(jwks_guard._unknown_kids) <= 8


def test_install_is_idempotent_and_patches_every_lookup_site():
    """Both call sites do `from ... import`, so the defining module is not enough."""
    from supertokens_python.recipe.session import access_token, jwks

    original = jwks.get_latest_keys
    jwks_guard.install_jwks_guard()
    jwks_guard.install_jwks_guard()

    assert access_token.get_latest_keys is jwks_guard._guarded_get_latest_keys
    assert jwks.get_latest_keys is jwks_guard._guarded_get_latest_keys

    jwks_guard.reset_jwks_guard_for_test()
    assert jwks.get_latest_keys is original


def test_a_transport_failure_is_not_cached_as_a_bad_kid(monkeypatch):
    """A blip must not answer 401 to a valid token for the whole TTL.

    The upstream raises a bare Exception when it fetched and the kid was
    genuinely absent, and a requests error when it could not fetch at all.
    Caching the second would turn an unreachable core into an outage for every
    key it had not already seen.
    """
    from requests import ConnectionError as RequestsConnectionError

    attempts: list[str | None] = []

    def _flaky(config, kid=None):
        attempts.append(kid)
        raise RequestsConnectionError("core unreachable")

    jwks_guard.install_jwks_guard()
    monkeypatch.setattr(jwks_guard, "_original_get_latest_keys", _flaky)

    for _ in range(3):
        with pytest.raises(RequestsConnectionError):
            jwks_guard._guarded_get_latest_keys(object(), "valid-kid")

    assert len(attempts) == 3, "a transport failure was cached as a bad kid"
    assert jwks_guard._unknown_kids == {}
