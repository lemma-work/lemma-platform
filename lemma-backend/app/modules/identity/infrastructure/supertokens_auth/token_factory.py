"""SuperTokens delegation payload helpers."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    CLAIM_ACTOR_NAME,
    CLAIM_ACTOR_TYPE,
    CLAIM_DELEGATION_VERSION,
    CLAIM_EXPIRES_AT,
    CLAIM_INVOKED_BY_USER_ID,
    CLAIM_ISSUED_AT,
    CLAIM_POD_ID,
    CLAIM_SCOPE,
    CLAIM_SESSION_ID,
    DELEGATION_VERSION,
    parse_delegation_claims,
)


def build_delegation_claims(
    *,
    workload_type: str,
    workload_id: UUID,
    pod_id: UUID,
    session_id: str,
    invoked_by_user_id: UUID,
    workload_name: str | None = None,
    scope: list[str] | None = None,
    issued_at: float | None = None,
    expires_at: float | None = None,
) -> dict:
    claims = {
        CLAIM_ACTOR_TYPE: workload_type,
        CLAIM_ACTOR_ID: str(workload_id),
        CLAIM_POD_ID: str(pod_id),
        CLAIM_SESSION_ID: session_id,
        CLAIM_SCOPE: scope or [],
        CLAIM_INVOKED_BY_USER_ID: str(invoked_by_user_id),
        CLAIM_DELEGATION_VERSION: DELEGATION_VERSION,
    }
    if workload_name:
        claims[CLAIM_ACTOR_NAME] = workload_name
    if issued_at is not None and expires_at is not None:
        claims[CLAIM_ISSUED_AT] = issued_at
        claims[CLAIM_EXPIRES_AT] = expires_at
    return claims


def validate_delegation_claims_payload(claims: dict) -> dict:
    parsed = parse_delegation_claims(claims)
    if parsed is None:
        return claims

    return {
        CLAIM_ACTOR_TYPE: parsed.actor_type.value,
        CLAIM_ACTOR_ID: str(parsed.actor_id),
        **({CLAIM_ACTOR_NAME: parsed.actor_name} if parsed.actor_name else {}),
        CLAIM_POD_ID: str(parsed.pod_id),
        CLAIM_SESSION_ID: parsed.session_id,
        CLAIM_SCOPE: parsed.scope,
        CLAIM_INVOKED_BY_USER_ID: str(parsed.invoked_by_user_id),
        CLAIM_DELEGATION_VERSION: parsed.delegation_version,
        **(
            {CLAIM_ISSUED_AT: parsed.issued_at, CLAIM_EXPIRES_AT: parsed.expires_at}
            if parsed.expires_at is not None
            else {}
        ),
    }
