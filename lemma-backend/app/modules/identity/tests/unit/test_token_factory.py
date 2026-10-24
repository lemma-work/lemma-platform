from __future__ import annotations

from uuid import uuid4

from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
    CLAIM_SESSION_ID,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
    validate_delegation_claims_payload,
)


def test_build_delegation_claims_contains_required_fields():
    claims = build_delegation_claims(
        workload_type="function",
        workload_id=uuid4(),
        pod_id=uuid4(),
        session_id="sess-1",
        invoked_by_user_id=uuid4(),
        workload_name="etl_worker",
        scope=["datastore.read", "document.read"],
    )

    assert CLAIM_ACTOR_TYPE in claims
    assert CLAIM_ACTOR_ID in claims
    assert CLAIM_POD_ID in claims
    assert CLAIM_SESSION_ID in claims
    assert CLAIM_SCOPE in claims
    assert CLAIM_INVOKED_BY_USER_ID in claims
    assert claims[CLAIM_ACTOR_NAME] == "etl_worker"
    assert claims[CLAIM_SESSION_ID] == "sess-1"


def test_validate_delegation_claims_payload_keeps_plain_payload():
    payload = {"foo": "bar"}

    assert validate_delegation_claims_payload(payload) == payload


def test_a_token_minted_with_a_lifetime_carries_it():
    """`ttl_seconds` is what makes a sandbox session's token short-lived."""
    import time
    from uuid import uuid4

    from app.core.authorization.delegation import CLAIM_EXPIRES_AT, CLAIM_ISSUED_AT
    from app.modules.identity.contracts.delegated_tokens import _delegation_claims

    before = time.time()
    claims = _delegation_claims(
        user_id=uuid4(),
        workload_type="AGENT",
        workload_id=uuid4(),
        pod_id=uuid4(),
        session_id="conversation",
        workload_name=None,
        scope=None,
        delegated_tokens_enabled=True,
        ttl_seconds=600,
    )

    assert claims is not None
    assert before <= claims[CLAIM_ISSUED_AT] <= time.time()
    assert claims[CLAIM_EXPIRES_AT] == claims[CLAIM_ISSUED_AT] + 600


def test_a_token_minted_without_one_carries_none():
    from uuid import uuid4

    from app.core.authorization.delegation import CLAIM_EXPIRES_AT
    from app.modules.identity.contracts.delegated_tokens import _delegation_claims

    claims = _delegation_claims(
        user_id=uuid4(),
        workload_type="AGENT",
        workload_id=uuid4(),
        pod_id=uuid4(),
        session_id="conversation",
        workload_name=None,
        scope=None,
        delegated_tokens_enabled=True,
    )

    assert claims is not None and CLAIM_EXPIRES_AT not in claims
