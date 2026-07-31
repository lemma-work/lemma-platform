from __future__ import annotations

import json

import pytest

from app.modules.identity.infrastructure.supertokens_auth import abuse_middleware
from app.modules.identity.infrastructure.supertokens_auth.abuse_middleware import (
    AuthAbuseMiddleware,
)
from app.modules.identity.services.auth_abuse import RateLimitExceeded


class _Store:
    def __init__(self, *, reject=False):
        self.reject = reject
        self.enforced: list[tuple[str, int, int]] = []
        self.proofs: list[tuple[str | None, str]] = []

    def digest(self, value):
        return f"digest-{value}"

    async def enforce(self, key, *, limit, window_seconds):
        self.enforced.append((key, limit, window_seconds))
        if self.reject:
            raise RateLimitExceeded(42)

    async def verify_altcha(self, payload, *, purpose):
        self.proofs.append((payload, purpose))

    async def count(self, _key):
        return 0

    async def clear(self, *_keys):
        return None


def _scope(path="/auth/signup", *, root_path="", method="POST"):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": root_path,
        "client": ("203.0.113.7", 1234),
        "headers": [(b"x-altcha-payload", b"proof")],
    }


def _receive(body):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


@pytest.mark.asyncio
async def test_signup_wires_all_ip_email_limits_and_altcha(monkeypatch):
    store = _Store()
    monkeypatch.setattr(abuse_middleware, "get_auth_abuse_store", lambda: store)

    async def inner(scope, receive, send):
        del scope
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b'{"status":"OK"}',
                "more_body": False,
            }
        )

    body = json.dumps(
        {
            "formFields": [
                {"id": "email", "value": "Person@Example.com"},
                {"id": "password", "value": "password"},
            ]
        }
    ).encode()
    messages = []

    async def send(message):
        messages.append(message)

    await AuthAbuseMiddleware(inner)(_scope(), _receive(body), send)

    configured_limits = {(limit, window) for _, limit, window in store.enforced}
    assert configured_limits == {
        (60, 60),
        (5, 900),
        (20, 86_400),
        (3, 900),
        (6, 86_400),
    }
    keys = {key for key, _, _ in store.enforced}
    assert any("digest-person@example.com" in key for key in keys)
    assert store.proofs == [("proof", "signup")]
    assert messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_signup_limits_apply_when_auth_app_is_mounted(monkeypatch):
    store = _Store()
    monkeypatch.setattr(abuse_middleware, "get_auth_abuse_store", lambda: store)

    async def inner(_scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b'{"status":"OK"}',
                "more_body": False,
            }
        )

    body = json.dumps(
        {
            "formFields": [
                {"id": "email", "value": "mounted@example.com"},
                {"id": "password", "value": "password"},
            ]
        }
    ).encode()

    async def send(_message):
        return None

    await AuthAbuseMiddleware(inner)(
        _scope("/st/auth/signup", root_path="/st"),
        _receive(body),
        send,
    )

    assert store.proofs == [("proof", "signup")]
    assert len(store.enforced) == 5


@pytest.mark.asyncio
async def test_auth_paths_only_applies_global_limit_to_custom_auth_routes(monkeypatch):
    store = _Store()
    monkeypatch.setattr(abuse_middleware, "get_auth_abuse_store", lambda: store)
    called = 0

    async def inner(_scope, _receive, send):
        nonlocal called
        called += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(_message):
        return None

    middleware = AuthAbuseMiddleware(inner, auth_paths_only=True)
    await middleware(_scope("/auth/altcha/challenge"), _receive(b""), send)
    assert len(store.enforced) == 1

    store.enforced.clear()
    await middleware(_scope("/users/me"), _receive(b""), send)
    assert store.enforced == []
    assert called == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/auth/user/email/verify", "GET"),
        ("/auth/signout", "POST"),
    ],
)
async def test_session_recovery_paths_bypass_exhausted_global_limit(
    monkeypatch, path, method
):
    store = _Store(reject=True)
    monkeypatch.setattr(abuse_middleware, "get_auth_abuse_store", lambda: store)
    called = False
    messages = []

    async def inner(_scope, _receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def send(message):
        messages.append(message)

    await AuthAbuseMiddleware(inner)(
        _scope(path, method=method),
        _receive(b""),
        send,
    )

    assert called is True
    assert store.enforced == []
    assert messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_rate_limit_rejection_returns_429_and_retry_after(monkeypatch):
    store = _Store(reject=True)
    monkeypatch.setattr(abuse_middleware, "get_auth_abuse_store", lambda: store)
    called = False

    async def inner(_scope, _receive, _send):
        nonlocal called
        called = True

    messages = []

    async def send(message):
        messages.append(message)

    await AuthAbuseMiddleware(inner)(_scope(), _receive(b"{}"), send)

    assert called is False
    assert messages[0]["status"] == 429
    headers = dict(messages[0]["headers"])
    assert headers[b"retry-after"] == b"42"
