"""When delegated tokens are disabled, verify_auth must reject impersonation /
delegation tokens rather than silently honoring them as full user sessions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from supertokens_python.recipe.session.exceptions import (
    ClaimValidationError,
    InvalidClaimsError,
)

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


def test_signed_bounce_webhooks_are_public_but_other_auth_posts_are_not():
    assert security._is_public_identity_auth_path("/auth/email/bounces", "POST")
    assert security._is_public_identity_auth_path("/auth/email/bounces/resend", "POST")
    assert not security._is_public_identity_auth_path(
        "/auth/email/bounces/resend", "GET"
    )
    assert not security._is_public_identity_auth_path("/auth/verify-token", "POST")


def test_altcha_config_and_challenges_are_public():
    assert security._is_public_identity_auth_path("/auth/altcha/config", "GET")
    assert security._is_public_identity_auth_path("/auth/altcha/challenge", "GET")
    assert not security._is_public_identity_auth_path("/auth/altcha/config", "POST")


def _patch(monkeypatch, *, flag: bool, payload: dict):
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", flag, raising=False
    )
    monkeypatch.setattr(
        security,
        "_get_local_auth_state",
        AsyncMock(
            return_value=SimpleNamespace(
                is_active=True, is_verified=True, is_deleted=False
            )
        ),
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
async def test_invalid_session_claims_are_not_rewritten_as_unauthorised(monkeypatch):
    invalid_claims = InvalidClaimsError(
        "INVALID_CLAIMS",
        [ClaimValidationError("st-ev", {"actualValue": False})],
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(side_effect=invalid_claims),
    )

    with pytest.raises(InvalidClaimsError) as exc:
        await security.verify_auth(_connection())

    assert exc.value is invalid_claims


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_state", "code"),
    [
        (
            SimpleNamespace(is_active=False, is_verified=True, is_deleted=False),
            "ACCOUNT_INACTIVE",
        ),
        (
            SimpleNamespace(is_active=True, is_verified=False, is_deleted=False),
            "EMAIL_VERIFICATION_REQUIRED",
        ),
        (
            SimpleNamespace(is_active=True, is_verified=True, is_deleted=True),
            "ACCOUNT_INACTIVE",
        ),
    ],
)
async def test_local_account_state_blocks_application_access(
    monkeypatch, local_state, code
):
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", False, raising=False
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(return_value=_FakeSession(str(uuid4()), {})),
    )
    monkeypatch.setattr(
        security, "_get_local_auth_state", AsyncMock(return_value=local_state)
    )

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(_connection())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == code


@pytest.mark.asyncio
async def test_unverified_account_is_allowed_when_verification_is_optional(monkeypatch):
    conn = _connection()
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", False, raising=False
    )
    monkeypatch.setattr(
        security.settings, "auth_email_verification_required", False, raising=False
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(return_value=_FakeSession(str(uuid4()), {})),
    )
    monkeypatch.setattr(
        security,
        "_get_local_auth_state",
        AsyncMock(
            return_value=SimpleNamespace(
                is_active=True, is_verified=False, is_deleted=False
            )
        ),
    )

    await security.verify_auth(conn)

    assert conn.state.user is not None


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
    monkeypatch.setattr(
        security,
        "_get_local_auth_state",
        AsyncMock(
            return_value=SimpleNamespace(
                is_active=True, is_verified=True, is_deleted=False
            )
        ),
    )

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
    monkeypatch.setattr(
        security,
        "_get_local_auth_state",
        AsyncMock(
            return_value=SimpleNamespace(
                is_active=True, is_verified=True, is_deleted=False
            )
        ),
    )

    await security.verify_auth(conn)

    assert conn.state.delegation_claims is not None
    assert str(conn.state.delegation_claims.actor_id) == actor_id
