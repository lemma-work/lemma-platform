"""build_context_from_delegation_claims must refuse to delegate for a different
user than the authenticated session (the claim's invoked_by_user_id and the
session user are minted together, so a mismatch is tampering or a bug)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.delegation import DelegationClaims, WorkloadPrincipalType
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError


def _claims(*, invoked_by_user_id) -> DelegationClaims:
    return DelegationClaims(
        actor_type=WorkloadPrincipalType.AGENT,
        actor_id=uuid4(),
        pod_id=uuid4(),
        session_id="sess",
        scope=[],
        invoked_by_user_id=invoked_by_user_id,
        delegation_version=1,
    )


@pytest.mark.asyncio
async def test_rejects_when_invoked_by_does_not_match_session_user():
    # The mismatch is caught before any DB work, so a mock session is fine.
    service = AuthorizationDataService(AsyncMock())

    with pytest.raises(DomainError) as exc:
        await service.build_context_from_delegation_claims(
            user_id=uuid4(),
            claims=_claims(invoked_by_user_id=uuid4()),
        )

    assert exc.value.status_code == 403
    assert exc.value.code == "DELEGATION_USER_MISMATCH"
