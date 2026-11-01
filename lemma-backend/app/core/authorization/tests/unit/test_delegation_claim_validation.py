"""Delegation JWT claims reject malformed values instead of coercing them."""

import pytest

from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_DELEGATION_VERSION,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
    CLAIM_SESSION_ID,
    DELEGATION_VERSION,
    DelegationClaimsError,
    parse_delegation_claims,
)


def _payload() -> dict[str, object]:
    return {
        CLAIM_ACTOR_TYPE: "AGENT",
        CLAIM_ACTOR_ID: "11111111-1111-4111-8111-111111111111",
        CLAIM_POD_ID: "22222222-2222-4222-8222-222222222222",
        CLAIM_SESSION_ID: "session-1",
        CLAIM_INVOKED_BY_USER_ID: "33333333-3333-4333-8333-333333333333",
        CLAIM_DELEGATION_VERSION: DELEGATION_VERSION,
        CLAIM_SCOPE: ["agent.read"],
    }


@pytest.mark.parametrize("version", [True, False, 1.0, 1.9, "1", [], {}])
def test_rejects_non_integer_delegation_versions(version: object) -> None:
    payload = _payload()
    payload[CLAIM_DELEGATION_VERSION] = version

    with pytest.raises(DelegationClaimsError, match="Invalid delegation version"):
        parse_delegation_claims(payload)


@pytest.mark.parametrize("session_id", [1, True, [], {}, "   "])
def test_rejects_non_string_or_blank_session_ids(session_id: object) -> None:
    payload = _payload()
    payload[CLAIM_SESSION_ID] = session_id

    with pytest.raises(DelegationClaimsError, match="Invalid delegated session id"):
        parse_delegation_claims(payload)


@pytest.mark.parametrize("actor_name", [1, True, [], {}])
def test_rejects_non_string_actor_names(actor_name: object) -> None:
    payload = _payload()
    payload[CLAIM_ACTOR_NAME] = actor_name

    with pytest.raises(DelegationClaimsError, match="Invalid delegated actor name"):
        parse_delegation_claims(payload)


@pytest.mark.parametrize("scope", [["agent.read", ""], ["  "], [1], {}])
def test_rejects_malformed_scope_entries(scope: object) -> None:
    payload = _payload()
    payload[CLAIM_SCOPE] = scope

    with pytest.raises(DelegationClaimsError, match="Invalid delegated scope"):
        parse_delegation_claims(payload)


def test_accepts_current_claim_shape_and_legacy_single_scope_string() -> None:
    payload = _payload()
    payload[CLAIM_SCOPE] = "agent.read"

    claims = parse_delegation_claims(payload)

    assert claims is not None
    assert claims.scope == ["agent.read"]
    assert claims.delegation_version == DELEGATION_VERSION


@pytest.mark.parametrize(
    "partial_claim",
    [
        CLAIM_ACTOR_NAME,
        CLAIM_POD_ID,
        CLAIM_SESSION_ID,
        CLAIM_SCOPE,
        CLAIM_INVOKED_BY_USER_ID,
        CLAIM_DELEGATION_VERSION,
    ],
)
def test_rejects_companion_claims_without_delegated_actor_identity(
    partial_claim: str,
) -> None:
    payload: dict[str, object] = {partial_claim: "present"}

    with pytest.raises(DelegationClaimsError, match="Incomplete delegated claims"):
        parse_delegation_claims(payload)


def test_plain_person_claims_without_delegation_namespace_remain_plain() -> None:
    assert parse_delegation_claims({"sub": "user-1", "exp": 2_000_000_000}) is None
