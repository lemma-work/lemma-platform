"""What a pod may decide on its own, and what belongs to the organization.

Three rules that meet at the pod/organization boundary:

- opening a pod to everyone mints organization membership, so a pod editor may
  not do it and no pod may open wider than its organization (ACCESS-07);
- the last person who can administer a pod is counted by permission, and so is
  the trigger that protects them (ACCESS-08);
- a delegated workload carries its invoker's authority on any path, so the
  organization routes have to turn it away themselves (ACCESS-06).
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
            "description": "org boundary e2e",
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
        session_id=f"org-boundary-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_an_editor_cannot_open_a_pod_to_everyone(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """ACCESS-07: `PUT /pods/{id}` merges config field-wise and needed only
    `pod.update`, so an editor could set `join_policy: PUBLIC` -- which mints an
    ORG_MEMBER row for any signed-in account that then joins."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Boundary Pod")

    editor = await signup_user(async_client, "boundary-editor")
    editor_org = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=editor
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=editor_org["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR"],
    )

    opened = await async_client.put(
        f"/pods/{pod_id}",
        json={"config": {"join_policy": "PUBLIC"}},
        headers=auth_headers(editor),
    )
    assert opened.status_code == status.HTTP_403_FORBIDDEN, opened.text

    # An editor's actual job is untouched.
    renamed = await async_client.put(
        f"/pods/{pod_id}",
        json={"description": "renamed by the editor"},
        headers=auth_headers(editor),
    )
    assert renamed.status_code == status.HTTP_200_OK, renamed.text

    # And the pod's own admin is still bounded by the organization: this
    # organization is not public, so neither may the pod be.
    by_owner = await authenticated_client.put(
        f"/pods/{pod_id}",
        json={"config": {"join_policy": "PUBLIC"}},
    )
    assert by_owner.status_code == status.HTTP_403_FORBIDDEN, by_owner.text

    to_the_org = await authenticated_client.put(
        f"/pods/{pod_id}",
        json={"config": {"join_policy": "ORG_MEMBERS"}},
    )
    assert to_the_org.status_code == status.HTTP_200_OK, to_the_org.text
    assert to_the_org.json()["config"]["join_policy"] == "ORG_MEMBERS"


@pytest.mark.asyncio
async def test_the_last_administrator_is_counted_by_permission_not_by_name(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    """ACCESS-08: the count went by permission, the trigger by role name.

    Both halves of that mismatch are here, in the order that produces them: a
    POD_ADMIN who is the pod's only administrator moves to a custom role
    carrying ``pod.member.manage`` (refused before, because the destination was
    not called POD_ADMIN), and then cannot leave the pod (allowed before,
    because the departing member was not called POD_ADMIN either -- which left
    the pod with nobody who could manage it).
    """
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Last Admin Pod")

    custodian = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": "custodian", "permission_ids": ["pod.member.manage"]},
    )
    assert custodian.status_code == status.HTTP_201_CREATED, custodian.text

    # The pod's creator is its only POD_ADMIN.
    owner_member = await authenticated_client.get(
        f"/pods/{pod_id}/members/lookup/by-user-id/{fixed_test_user['id']}"
    )
    assert owner_member.status_code == status.HTTP_200_OK, owner_member.text
    owner_member_id = owner_member.json()["pod_member_id"]

    renamed = await authenticated_client.patch(
        f"/pods/{pod_id}/members/{owner_member_id}/roles",
        json={"roles": ["POD_ADMIN", "CUSTODIAN"]},
    )
    assert renamed.status_code == status.HTTP_200_OK, renamed.text

    # Dropping POD_ADMIN while keeping a role that administers the pod leaves
    # the pod with an administrator, so there is nothing to refuse.
    moved = await authenticated_client.patch(
        f"/pods/{pod_id}/members/{owner_member_id}/roles",
        json={"roles": ["POD_USER", "CUSTODIAN"]},
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text
    assert set(moved.json()["roles"]) == {"POD_USER", "CUSTODIAN"}

    # And now the guard has to fire for a member who is not called POD_ADMIN:
    # they are the only one who can manage this pod.
    left = await authenticated_client.delete(
        f"/pods/{pod_id}/members/{owner_member_id}"
    )
    assert left.status_code == status.HTTP_409_CONFLICT, left.text


@pytest.mark.asyncio
async def test_a_delegated_workload_cannot_reach_the_organization_routes(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    """ACCESS-06: the organization API authorizes on the member row and never
    consults the request Context, so a delegated token was accepted there with
    the full authority of whoever invoked the workload."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Delegated Org Pod")
    agent_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id
    )

    invite = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={
            "email": f"test+delegated-{uuid4().hex[:8]}@example.com",
            "role": "ORG_OWNER",
        },
        headers=agent_headers,
    )
    assert invite.status_code == status.HTTP_403_FORBIDDEN, invite.text
    assert invite.json()["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"

    renamed = await async_client.patch(
        f"/organizations/{org_id}",
        json={"name": "Owned By The Agent"},
        headers=agent_headers,
    )
    assert renamed.status_code == status.HTTP_403_FORBIDDEN, renamed.text

    members = await authenticated_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == status.HTTP_200_OK, members.text
    owner_member_id = members.json()["items"][0]["id"]

    promoted = await async_client.patch(
        f"/organizations/{org_id}/members/{owner_member_id}/role",
        json={"role": "ORG_OWNER"},
        headers=agent_headers,
    )
    assert promoted.status_code == status.HTTP_403_FORBIDDEN, promoted.text

    removed = await async_client.delete(
        f"/organizations/{org_id}/members/{owner_member_id}",
        headers=agent_headers,
    )
    assert removed.status_code == status.HTTP_403_FORBIDDEN, removed.text

    # Reading the organization is unaffected: this closes the mutating routes,
    # not the workload's ability to know where it is.
    read = await async_client.get(f"/organizations/{org_id}", headers=agent_headers)
    assert read.status_code == status.HTTP_200_OK, read.text
