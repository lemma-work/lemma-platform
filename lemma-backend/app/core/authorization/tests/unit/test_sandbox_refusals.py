"""What a workload's token is refused over HTTP, remembered for its session.

The sandbox's ``lemma`` CLI calls the API with the workload's token; the refusal
it gets is the only server-side account of what a sandbox command needs. These
pin that the account is written from the refusal itself, under the key the
authorizer reads approvals back by, and that only approvable refusals count.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization import session_approvals
from app.core.authorization.delegation import (
    DelegationClaims,
    WorkloadPrincipalType,
)

pytestmark = pytest.mark.unit


class _FakeCache:
    def __init__(self):
        self.data: dict[str, object] = {}

    async def set_json(self, suffix, value, ttl_seconds=None):
        self.data[suffix] = value

    async def get_json(self, suffix):
        return self.data.get(suffix)

    async def delete(self, suffix):
        self.data.pop(suffix, None)


@pytest.fixture
def store(monkeypatch) -> _FakeCache:
    cache = _FakeCache()
    monkeypatch.setattr(session_approvals, "_get_approval_cache", lambda: cache)
    return cache


def _claims(**overrides) -> DelegationClaims:
    fields = {
        "actor_type": WorkloadPrincipalType.AGENT,
        "actor_id": uuid4(),
        "pod_id": uuid4(),
        "session_id": str(uuid4()),
        "invoked_by_user_id": uuid4(),
        "delegation_version": 1,
    }
    fields.update(overrides)
    return DelegationClaims(**fields)


def _key(claims: DelegationClaims) -> dict:
    return {
        "session_id": claims.session_id,
        "person_user_id": claims.invoked_by_user_id,
        "workload_actor_id": f"agent:{claims.actor_id}",
    }


@pytest.mark.asyncio
async def test_a_destructive_refusal_is_remembered_for_the_token_s_session(store):
    claims = _claims()

    await session_approvals.record_delegated_refusal(
        claims,
        code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        details={"permission_ids": ["datastore.table.delete"]},
    )
    await session_approvals.record_delegated_refusal(
        claims,
        code="MISSING_WORKLOAD_RESOURCE_GRANT",
        details={"permission_ids": ["datastore.record.write"]},
    )

    assert await session_approvals.session_refusals(**_key(claims)) == {
        "datastore.table.delete",
        "datastore.record.write",
    }


@pytest.mark.asyncio
async def test_a_refusal_no_approval_could_lift_is_not_remembered(store):
    """The person's own ceiling, a narrowed scope: asking cannot help."""
    claims = _claims()

    for code in ("DELEGATION_EXCEEDS_INVOKER", "DELEGATION_SCOPE_VIOLATION"):
        await session_approvals.record_delegated_refusal(
            claims, code=code, details={"permission_ids": ["pod.delete"]}
        )

    assert await session_approvals.session_refusals(**_key(claims)) == frozenset()


@pytest.mark.asyncio
async def test_one_person_s_sandbox_refusals_are_not_another_s(store):
    claims = _claims()
    await session_approvals.record_delegated_refusal(
        claims,
        code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        details={"permission_ids": ["datastore.table.delete"]},
    )

    other = {**_key(claims), "person_user_id": uuid4()}

    assert await session_approvals.session_refusals(**other) == frozenset()


@pytest.mark.asyncio
async def test_an_answered_refusal_is_cleared_and_the_rest_kept(store):
    claims = _claims()
    await session_approvals.record_delegated_refusal(
        claims,
        code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        details={"permission_ids": ["datastore.table.delete", "folder.delete"]},
    )

    await session_approvals.clear_session_refusals(
        **_key(claims), permission_ids={"datastore.table.delete"}
    )

    assert await session_approvals.session_refusals(**_key(claims)) == {"folder.delete"}


@pytest.mark.asyncio
async def test_a_revoked_approval_is_gone(store):
    key = {
        "session_id": "s",
        "person_user_id": uuid4(),
        "workload_actor_id": "agent:x",
        "permission_id": "datastore.table.delete",
    }
    await session_approvals.record_session_approval(**key)

    await session_approvals.revoke_session_approval(**key)

    assert not await session_approvals.has_session_approval(**key)
