"""A route that requires a person refuses a workload's token, and only that.

Member-management / membership-minting endpoints declare ``require_person`` so a
delegated agent cannot confer membership on the invoking user's behalf, while
human admins are unaffected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.api.actor_policy import WORKLOAD_NOT_ALLOWED, require_person
from app.core.domain.errors import DomainError


def _request(delegation_claims: object | None) -> SimpleNamespace:
    """Just the part of a ``Request`` the gate reads."""
    return SimpleNamespace(state=SimpleNamespace(delegation_claims=delegation_claims))


@pytest.mark.asyncio
async def test_a_workload_token_is_refused():
    dependency = require_person("approve join requests").dependency

    with pytest.raises(DomainError) as exc:
        await dependency(_request({"actor_type": "AGENT"}))

    assert exc.value.status_code == 403
    assert exc.value.code == WORKLOAD_NOT_ALLOWED
    assert "approve join requests" in str(exc.value)


@pytest.mark.asyncio
async def test_a_person_passes():
    dependency = require_person("approve join requests").dependency

    # No raise for a human actor.
    await dependency(_request(None))


@pytest.mark.asyncio
async def test_a_request_that_never_set_claims_passes():
    dependency = require_person("approve join requests").dependency

    await dependency(SimpleNamespace(state=SimpleNamespace()))
