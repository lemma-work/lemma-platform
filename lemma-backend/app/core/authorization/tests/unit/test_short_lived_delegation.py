"""A delegated token with a lifetime of its own.

A workspace sandbox is one Linux user shared by every session of every agent,
so a token delivered there can be read by the others. Such a token carries when
it was issued and when it stops counting; every entry point refuses it after
that, or once its delegation session -- its conversation -- has ended.
"""

from __future__ import annotations

import time
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
    CLAIM_EXPIRES_AT,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_ISSUED_AT,
    CLAIM_POD_ID,
    CLAIM_SESSION_ID,
    DELEGATION_VERSION,
    DelegationClaimsError,
    parse_delegation_claims,
)
from app.core.security import PrincipalLookups, PrincipalRefused
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
    validate_delegation_claims_payload,
)


def _payload(user_id, **lifetime) -> dict[str, object]:
    return {
        CLAIM_ACTOR_TYPE: "AGENT",
        CLAIM_ACTOR_ID: str(uuid4()),
        CLAIM_POD_ID: str(uuid4()),
        CLAIM_SESSION_ID: "conversation",
        CLAIM_INVOKED_BY_USER_ID: str(user_id),
        CLAIM_DELEGATION_VERSION: DELEGATION_VERSION,
        "isImpersonation": True,
        **lifetime,
    }


def _lookups(*, ended: bool = False) -> tuple[PrincipalLookups, AsyncMock]:
    session_ended = AsyncMock(return_value=ended)
    return (
        PrincipalLookups(
            account_standing=AsyncMock(
                return_value=SimpleNamespace(
                    is_active=True, is_verified=True, is_deleted=False
                )
            ),
            delegation_revoked=AsyncMock(return_value=False),
            session_ended=session_ended,
        ),
        session_ended,
    )


@pytest.fixture(autouse=True)
def _delegation_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.settings, "authz_delegated_tokens_enabled", True)


def test_a_lifetime_is_parsed_and_survives_the_token_factory() -> None:
    user_id = uuid4()
    claims = build_delegation_claims(
        workload_type="AGENT",
        workload_id=uuid4(),
        pod_id=uuid4(),
        session_id="conversation",
        invoked_by_user_id=user_id,
        issued_at=100.5,
        expires_at=1000.5,
    )

    validated = validate_delegation_claims_payload(claims)
    parsed = parse_delegation_claims(validated)

    assert parsed is not None
    assert (parsed.issued_at, parsed.expires_at) == (100.5, 1000.5)


def test_a_token_without_a_lifetime_is_unchanged() -> None:
    claims = build_delegation_claims(
        workload_type="AGENT",
        workload_id=uuid4(),
        pod_id=uuid4(),
        session_id="s",
        invoked_by_user_id=uuid4(),
    )

    assert CLAIM_EXPIRES_AT not in validate_delegation_claims_payload(claims)
    parsed = parse_delegation_claims(claims)
    assert parsed is not None and parsed.expires_at is None


@pytest.mark.parametrize(
    "lifetime",
    [
        {CLAIM_EXPIRES_AT: 10.0},
        {CLAIM_ISSUED_AT: 1.0},
        {CLAIM_ISSUED_AT: 1.0, CLAIM_EXPIRES_AT: "soon"},
        {CLAIM_ISSUED_AT: True, CLAIM_EXPIRES_AT: 10.0},
    ],
)
def test_half_a_lifetime_is_refused_rather_than_read_as_none(lifetime) -> None:
    """Read as "no lifetime", it would be the long-lived kind of token."""
    with pytest.raises(DelegationClaimsError):
        parse_delegation_claims(_payload(uuid4(), **lifetime))


@pytest.mark.asyncio
async def test_a_token_past_its_lifetime_is_refused() -> None:
    user_id = uuid4()
    now = time.time()
    lookups, _ = _lookups()

    with pytest.raises(PrincipalRefused) as refused:
        await security.resolve_delegated_principal(
            _payload(user_id, **{CLAIM_ISSUED_AT: now - 60, CLAIM_EXPIRES_AT: now - 1}),
            user_id=user_id,
            lookups=lookups,
        )

    assert refused.value.code == "DELEGATION_EXPIRED"


@pytest.mark.asyncio
async def test_a_token_whose_session_has_ended_is_refused() -> None:
    user_id = uuid4()
    now = time.time()
    lookups, session_ended = _lookups(ended=True)

    with pytest.raises(PrincipalRefused) as refused:
        await security.resolve_delegated_principal(
            _payload(user_id, **{CLAIM_ISSUED_AT: now, CLAIM_EXPIRES_AT: now + 600}),
            user_id=user_id,
            lookups=lookups,
        )

    assert refused.value.code == "DELEGATION_SESSION_ENDED"
    session_ended.assert_awaited_once_with("conversation", now)


@pytest.mark.asyncio
async def test_a_live_short_lived_token_is_honoured() -> None:
    user_id = uuid4()
    now = time.time()
    lookups, _ = _lookups()

    claims = await security.resolve_delegated_principal(
        _payload(user_id, **{CLAIM_ISSUED_AT: now, CLAIM_EXPIRES_AT: now + 600}),
        user_id=user_id,
        lookups=lookups,
    )

    assert claims is not None and claims.expires_at == now + 600


@pytest.mark.asyncio
async def test_a_token_without_a_lifetime_never_asks_about_its_session() -> None:
    """The read is paid only by tokens a session end can reach."""
    user_id = uuid4()
    lookups, session_ended = _lookups(ended=True)

    claims = await security.resolve_delegated_principal(
        _payload(user_id), user_id=user_id, lookups=lookups
    )

    assert claims is not None
    session_ended.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ended", [False, True])
async def test_a_spent_token_is_a_401_so_the_client_fetches_a_new_one(
    monkeypatch: pytest.MonkeyPatch, ended: bool
) -> None:
    """403 tells a client to stop; 401 sends the SDK back to its token file."""
    user_id = uuid4()
    now = time.time()
    lifetime = (
        {CLAIM_ISSUED_AT: now, CLAIM_EXPIRES_AT: now + 600}
        if ended
        else {CLAIM_ISSUED_AT: now - 60, CLAIM_EXPIRES_AT: now - 1}
    )
    session = SimpleNamespace(
        get_user_id=lambda: str(user_id),
        get_access_token_payload=lambda: _payload(user_id, **lifetime),
    )
    monkeypatch.setattr(security, "get_session", AsyncMock(return_value=session))
    monkeypatch.setattr(
        security, "is_delegation_revoked", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        security, "delegation_session_ended", AsyncMock(return_value=ended)
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
    connection = SimpleNamespace(
        url=SimpleNamespace(path="/pods/x"),
        scope={
            "type": "http",
            "method": "GET",
            "route": SimpleNamespace(path_format="/pods/{pod_id}"),
        },
        headers={},
        state=SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(connection)

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == (
        "DELEGATION_SESSION_ENDED" if ended else "DELEGATION_EXPIRED"
    )
