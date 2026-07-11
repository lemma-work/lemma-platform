"""When delegated tokens are disabled, verify_auth must reject impersonation /
delegation tokens rather than silently honoring them as full user sessions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core import security
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_TYPE,
    CLAIM_DELEGATION_VERSION,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_POD_ID,
    CLAIM_SESSION_ID,
    DELEGATION_VERSION,
)


class _FakeSession:
    def __init__(self, user_id: str, payload: dict):
        self._user_id = user_id
        self._payload = payload

    def get_user_id(self) -> str:
        return self._user_id

    def get_access_token_payload(self) -> dict:
        return self._payload


def _connection() -> SimpleNamespace:
    return SimpleNamespace(
        url=SimpleNamespace(path="/pods/does-not-matter"),
        scope={"type": "http", "method": "GET"},
        state=SimpleNamespace(),
    )


def test_only_desktop_request_creation_and_exchange_are_public():
    assert security._is_public_desktop_auth_path("/auth/desktop/requests", "POST")
    assert security._is_public_desktop_auth_path("/auth/desktop/session", "POST")
    assert not security._is_public_desktop_auth_path(
        "/auth/desktop/requests/request-id/complete", "POST"
    )
    assert not security._is_public_desktop_auth_path("/auth/desktop/requests", "GET")


def _patch(monkeypatch, *, flag: bool, payload: dict):
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", flag, raising=False
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(return_value=_FakeSession(str(uuid4()), payload)),
    )


@pytest.mark.asyncio
async def test_livez_bypasses_session_authentication(monkeypatch):
    conn = _connection()
    conn.url.path = "/livez"
    get_session = AsyncMock(side_effect=AssertionError("must not authenticate"))
    monkeypatch.setattr(security, "get_session", get_session)

    await security.verify_auth(conn)

    get_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_impersonation_token_rejected_when_delegation_disabled(monkeypatch):
    _patch(monkeypatch, flag=False, payload={"isImpersonation": True})

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(_connection())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "IMPERSONATION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_delegation_claim_token_rejected_when_delegation_disabled(monkeypatch):
    _patch(monkeypatch, flag=False, payload={CLAIM_ACTOR_ID: str(uuid4())})

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(_connection())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "IMPERSONATION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_plain_user_token_accepted_when_delegation_disabled(monkeypatch):
    conn = _connection()
    _patch(monkeypatch, flag=False, payload={"client": "lemma-cli"})

    await security.verify_auth(conn)

    assert conn.state.user is not None
    assert conn.state.delegation_claims is None


def _delegation_payload(*, user_id: str, actor_id: str) -> dict:
    return {
        CLAIM_ACTOR_TYPE: "AGENT",
        CLAIM_ACTOR_ID: actor_id,
        CLAIM_POD_ID: str(uuid4()),
        CLAIM_SESSION_ID: "sess",
        CLAIM_INVOKED_BY_USER_ID: user_id,
        CLAIM_DELEGATION_VERSION: DELEGATION_VERSION,
        "isImpersonation": True,
    }


@pytest.mark.asyncio
async def test_revoked_delegation_token_rejected(monkeypatch):
    user_id = str(uuid4())
    actor_id = str(uuid4())
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", True, raising=False
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(
            return_value=_FakeSession(
                user_id, _delegation_payload(user_id=user_id, actor_id=actor_id)
            )
        ),
    )
    monkeypatch.setattr(security, "is_delegation_revoked", AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(_connection())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "DELEGATION_REVOKED"


@pytest.mark.asyncio
async def test_live_delegation_token_accepted(monkeypatch):
    conn = _connection()
    user_id = str(uuid4())
    actor_id = str(uuid4())
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", True, raising=False
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(
            return_value=_FakeSession(
                user_id, _delegation_payload(user_id=user_id, actor_id=actor_id)
            )
        ),
    )
    monkeypatch.setattr(
        security, "is_delegation_revoked", AsyncMock(return_value=False)
    )

    await security.verify_auth(conn)

    assert conn.state.delegation_claims is not None
    assert str(conn.state.delegation_claims.actor_id) == actor_id
