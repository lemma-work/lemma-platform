"""Asking for one resource, and getting exactly that.

The point of this flow is what it does *not* do: approving a request must grant
the resource without making the requester a pod member. Before this existed the
only way to say yes was to approve a pod join request, which hands over the
whole pod — so these tests pin the blast radius as much as the happy path.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.test_support.e2e_authz import (
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _make_pod(owner_client: AsyncClient, org_id: str) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"access requests {uuid4().hex[:8]}",
            "description": "Resource access request e2e pod",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _make_file(owner_client: AsyncClient, pod_id: str) -> dict:
    response = await owner_client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/", "visibility": "POD", "search_enabled": "false"},
        files={"data": (f"ask_{uuid4().hex[:8]}.md", b"# Private\n", "text/markdown")},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _read(client: AsyncClient, pod_id: str, path: str, headers: dict) -> int:
    response = await client.get(
        f"/pods/{pod_id}/datastore/files/by-path",
        params={"path": path},
        headers=headers,
    )
    return response.status_code


@pytest.fixture
async def scenario(authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org):
    """A pod-visible file and a colleague who cannot read it."""
    pod_id = await _make_pod(authenticated_client, fixed_test_org["id"])
    file_payload = await _make_file(authenticated_client, pod_id)

    colleague = await signup_user(async_client, "ask-colleague")
    await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=colleague
    )

    return {
        "pod_id": pod_id,
        "path": file_payload["path"],
        "file_id": file_payload["id"],
        "colleague": auth_headers(colleague),
    }


async def _request_access(client: AsyncClient, scenario, headers: dict):
    return await client.post(
        f"/pods/{scenario['pod_id']}/resource-access-requests",
        json={
            "resource_type": "document",
            "resource_id": scenario["file_id"],
            "resource_name": scenario["path"],
            "message": "Need this for the quarterly review",
        },
        headers=headers,
    )


async def test_approving_grants_the_resource_without_pod_membership(
    authenticated_client: AsyncClient, async_client: AsyncClient, scenario
):
    pod_id = scenario["pod_id"]

    # Cannot read it to begin with.
    assert await _read(async_client, pod_id, scenario["path"], scenario["colleague"]) != (
        status.HTTP_200_OK
    )

    created = await _request_access(async_client, scenario, scenario["colleague"])
    assert created.status_code == status.HTTP_201_CREATED, created.text
    request_id = created.json()["id"]
    assert created.json()["status"] == "PENDING"
    # Read is all a guest may ask for.
    assert created.json()["requested_permission_ids"] == ["folder.read"]

    listed = await authenticated_client.get(f"/pods/{pod_id}/resource-access-requests")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert any(item["id"] == request_id for item in listed.json()["items"])

    approved = await authenticated_client.post(
        f"/pods/{pod_id}/resource-access-requests/{request_id}/approve"
    )
    assert approved.status_code == status.HTTP_200_OK, approved.text
    assert approved.json()["status"] == "APPROVED"

    # The resource is now readable...
    assert await _read(async_client, pod_id, scenario["path"], scenario["colleague"]) == (
        status.HTTP_200_OK
    )

    # ...and that is *all* that changed. No membership, so no walking the pod —
    # this is the difference from approving a join request.
    tree = await async_client.get(
        f"/pods/{pod_id}/datastore/files/tree",
        params={"root_path": "/"},
        headers=scenario["colleague"],
    )
    assert tree.status_code == status.HTTP_403_FORBIDDEN, tree.text

    members = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert members.status_code == status.HTTP_200_OK, members.text
    member_emails = {
        item.get("user_email") or item.get("email") for item in members.json()["items"]
    }
    assert not any(email and "ask-colleague" in str(email) for email in member_emails)


async def test_asking_twice_reuses_the_pending_request(
    async_client: AsyncClient, scenario
):
    # The guest view offers this on a page people refresh; a queue of identical
    # asks is worse for the person reading them than for the one sending them.
    first = await _request_access(async_client, scenario, scenario["colleague"])
    second = await _request_access(async_client, scenario, scenario["colleague"])

    assert first.status_code == status.HTTP_201_CREATED, first.text
    assert second.status_code == status.HTTP_201_CREATED, second.text
    assert first.json()["id"] == second.json()["id"]


async def test_rejecting_leaves_the_resource_unreadable(
    authenticated_client: AsyncClient, async_client: AsyncClient, scenario
):
    pod_id = scenario["pod_id"]
    created = await _request_access(async_client, scenario, scenario["colleague"])
    request_id = created.json()["id"]

    rejected = await authenticated_client.post(
        f"/pods/{pod_id}/resource-access-requests/{request_id}/reject"
    )
    assert rejected.status_code == status.HTTP_200_OK, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    assert await _read(async_client, pod_id, scenario["path"], scenario["colleague"]) != (
        status.HTTP_200_OK
    )


async def test_requester_cannot_decide_their_own_request(
    async_client: AsyncClient, scenario
):
    pod_id = scenario["pod_id"]
    created = await _request_access(async_client, scenario, scenario["colleague"])
    request_id = created.json()["id"]

    approved = await async_client.post(
        f"/pods/{pod_id}/resource-access-requests/{request_id}/approve",
        headers=scenario["colleague"],
    )

    assert approved.status_code == status.HTTP_403_FORBIDDEN, approved.text


async def test_unknown_resource_does_not_confirm_what_exists(
    async_client: AsyncClient, scenario
):
    response = await async_client.post(
        f"/pods/{scenario['pod_id']}/resource-access-requests",
        json={"resource_type": "document", "resource_id": str(uuid4())},
        headers=scenario["colleague"],
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text
