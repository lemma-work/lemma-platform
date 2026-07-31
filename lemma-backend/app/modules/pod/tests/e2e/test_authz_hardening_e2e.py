"""End-to-end coverage for the authorization-hardening fixes:

- join-request approval cannot grant an org role above the approver's authority;
- a removed pod member loses access on the very next request (no TTL wait);
- a delegated workload cannot manage members or approve join requests.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def _create_pod(owner_client: AsyncClient, org_id: str, name: str) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"{name} {uuid4().hex[:8]}",
            "description": "authz hardening e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _default_pod_agent_headers(*, user_id: str, pod_id: str) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=DEFAULT_POD_AGENT_ID,
        workload_name=DEFAULT_POD_AGENT_NAME,
        pod_id=UUID(pod_id),
        session_id=f"authz-hardening-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_pod_admin_cannot_mint_org_owner_via_join_approval(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """A POD_ADMIN who is only an ORG_MEMBER may approve join requests, but must
    not be able to grant the joining user an elevated org role."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Escalation Pod")

    approver = await signup_user(async_client, "approver")
    approver_org_member = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=approver
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=approver_org_member["id"],
        role="POD_ADMIN",
        roles=["POD_ADMIN"],
    )

    requester = await signup_user(async_client, "requester")
    create = await async_client.post(
        f"/pods/{pod_id}/join-requests", headers=auth_headers(requester)
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    join_request_id = create.json()["id"]

    # Escalation attempt: mint an org owner. Must be rejected.
    escalate = await async_client.post(
        f"/pods/{pod_id}/join-requests/{join_request_id}/approve",
        json={"org_role": "ORG_OWNER", "pod_role": "POD_VIEWER"},
        headers=auth_headers(approver),
    )
    assert escalate.status_code == status.HTTP_403_FORBIDDEN, escalate.text

    # Control: a permitted org role still approves.
    ok = await async_client.post(
        f"/pods/{pod_id}/join-requests/{join_request_id}/approve",
        json={"org_role": "ORG_MEMBER", "pod_role": "POD_VIEWER"},
        headers=auth_headers(approver),
    )
    assert ok.status_code == status.HTTP_200_OK, ok.text
    assert ok.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_removed_member_loses_access_immediately(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """Removing a member invalidates their cached role snapshot, so the next
    authorized request is denied rather than served from the stale snapshot."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Removal Pod")

    member = await signup_user(async_client, "removed")
    member_org = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=member
    )
    pod_member = await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=member_org["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR"],
    )

    # A pod.read-gated endpoint (goes through the cached role snapshot). This
    # first call populates the snapshot for the member.
    catalog_url = f"/pods/{pod_id}/permissions/catalog"
    before = await async_client.get(catalog_url, headers=auth_headers(member))
    assert before.status_code == status.HTTP_200_OK, before.text

    removed = await authenticated_client.delete(
        f"/pods/{pod_id}/members/{pod_member['pod_member_id']}"
    )
    assert removed.status_code == status.HTTP_204_NO_CONTENT, removed.text

    # Denied on the very next request — no waiting for the snapshot TTL.
    after = await async_client.get(catalog_url, headers=auth_headers(member))
    assert after.status_code == status.HTTP_403_FORBIDDEN, after.text


@pytest.mark.asyncio
async def test_default_pod_agent_cannot_add_member(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    """A delegated workload hits the destructive-action gate on member add."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Agent Add Pod")

    candidate = await signup_user(async_client, "candidate")
    candidate_org = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=candidate
    )

    agent_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id
    )
    response = await async_client.post(
        f"/pods/{pod_id}/members",
        json={
            "organization_member_id": candidate_org["id"],
            "roles": ["POD_VIEWER"],
        },
        headers=agent_headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"


@pytest.mark.asyncio
async def test_default_pod_agent_cannot_approve_join_request(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    """A delegated workload is denied outright on join-request approval."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Agent Approve Pod")

    requester = await signup_user(async_client, "agent-approve-req")
    create = await async_client.post(
        f"/pods/{pod_id}/join-requests", headers=auth_headers(requester)
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    join_request_id = create.json()["id"]

    agent_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id
    )
    response = await async_client.post(
        f"/pods/{pod_id}/join-requests/{join_request_id}/approve",
        json={"org_role": "ORG_MEMBER", "pod_role": "POD_VIEWER"},
        headers=agent_headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"
