from __future__ import annotations

import base64
import hashlib
import json

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.modules.identity.services.auth_abuse import (
    AltchaRejected,
    AuthAbuseStore,
    RateLimitExceeded,
    client_ip,
)


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.counts.pop(key, None)

    async def eval(self, _script, _number_of_keys, key, window):
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], int(window)]

    async def aclose(self):
        return None


def _store() -> AuthAbuseStore:
    store = AuthAbuseStore.__new__(AuthAbuseStore)
    store._redis = _FakeRedis()
    return store


def _solve(challenge: dict) -> str:
    number = next(
        candidate
        for candidate in range(challenge["maxnumber"] + 1)
        if hashlib.sha256(f"{challenge['salt']}{candidate}".encode()).hexdigest()
        == challenge["challenge"]
    )
    payload = {
        "algorithm": challenge["algorithm"],
        "challenge": challenge["challenge"],
        "number": number,
        "salt": challenge["salt"],
        "signature": challenge["signature"],
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


@pytest.mark.asyncio
async def test_altcha_proof_is_single_use_and_purpose_bound(monkeypatch):
    monkeypatch.setattr(settings, "auth_altcha_enabled", True)
    monkeypatch.setattr(settings, "auth_altcha_hmac_key", SecretStr("test-altcha-key"))
    monkeypatch.setattr(settings, "auth_altcha_max_number", 100)
    store = _store()

    challenge = await store.issue_altcha("signup")
    proof = _solve(challenge)

    await store.verify_altcha(proof, purpose="signup")
    with pytest.raises(AltchaRejected, match="already used"):
        await store.verify_altcha(proof, purpose="signup")

    wrong_purpose = await store.issue_altcha("verification")
    with pytest.raises(AltchaRejected, match="wrong purpose"):
        await store.verify_altcha(_solve(wrong_purpose), purpose="password-reset")


@pytest.mark.asyncio
async def test_rate_limit_boundary_returns_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "auth_abuse_protection_enabled", True)
    store = _store()

    await store.enforce("limit-key", limit=2, window_seconds=30)
    await store.enforce("limit-key", limit=2, window_seconds=30)
    with pytest.raises(RateLimitExceeded) as exc:
        await store.enforce("limit-key", limit=2, window_seconds=30)

    assert exc.value.retry_after_seconds == 30


def test_forwarded_ip_is_only_trusted_from_configured_proxy(monkeypatch):
    scope = {
        "client": ("10.0.0.10", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.8, 10.0.0.10")],
    }
    monkeypatch.setattr(settings, "auth_trusted_proxy_ips", [])
    assert client_ip(scope) == "10.0.0.10"

    monkeypatch.setattr(settings, "auth_trusted_proxy_ips", ["10.0.0.10"])
    assert client_ip(scope) == "203.0.113.8"
