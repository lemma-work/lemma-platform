"""Sharing with someone who does not have an account yet.

A resource grant keys on a user id, so before this existed "Specific access"
could only name people already in the pod. Sharing outward meant adding them to
the organization first — a much larger door than the one being asked for.

The invite holds the intended permissions against an email until an account
appears for it. These tests exercise that seam directly (rather than through the
signup event, which is delivered asynchronously by the worker) so the redemption
logic is pinned independently of the event plumbing.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.pod.services.resource_access_invite_service import (
    ResourceAccessInviteService,
)
from app.modules.test_support.e2e_authz import auth_headers, signup_user

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _make_pod(owner_client: AsyncClient, org_id: str) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"invites {uuid4().hex[:8]}",
            "description": "Resource access invite e2e pod",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _make_file(owner_client: AsyncClient, pod_id: str) -> dict:
    response = await owner_client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/", "visibility": "POD", "search_enabled": "false"},
        files={"data": (f"inv_{uuid4().hex[:8]}.md", b"# Invited\n", "text/markdown")},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def test_invite_is_recorded_listed_and_revocable(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await _make_pod(authenticated_client, fixed_test_org["id"])
    file_payload = await _make_file(authenticated_client, pod_id)
    email = f"future+{uuid4().hex[:8]}@example.com"

    created = await authenticated_client.post(
        f"/pods/{pod_id}/resource-access-invites",
        json={
            "resource_type": "document",
            "resource_id": file_payload["id"],
            "email": email,
            "permission_ids": ["folder.read"],
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    invite_id = created.json()["id"]
    assert created.json()["email"] == email
    assert created.json()["status"] == "PENDING"

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/resource-access-invites",
        params={"resource_type": "document", "resource_id": file_payload["id"]},
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [invite_id]

    revoked = await authenticated_client.delete(
        f"/pods/{pod_id}/resource-access-invites/{invite_id}"
    )
    assert revoked.status_code == status.HTTP_204_NO_CONTENT, revoked.text

    after = await authenticated_client.get(
        f"/pods/{pod_id}/resource-access-invites",
        params={"resource_type": "document", "resource_id": file_payload["id"]},
    )
    assert after.json()["items"] == []


async def test_reinviting_updates_rather_than_duplicating(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await _make_pod(authenticated_client, fixed_test_org["id"])
    file_payload = await _make_file(authenticated_client, pod_id)
    email = f"future+{uuid4().hex[:8]}@example.com"

    async def invite(permission_ids: list[str]):
        return await authenticated_client.post(
            f"/pods/{pod_id}/resource-access-invites",
            json={
                "resource_type": "document",
                "resource_id": file_payload["id"],
                "email": email,
                "permission_ids": permission_ids,
            },
        )

    first = await invite(["folder.read"])
    second = await invite(["folder.read", "folder.write"])

    assert first.json()["id"] == second.json()["id"]
    assert set(second.json()["permission_ids"]) == {"folder.read", "folder.write"}


async def test_redemption_turns_an_invite_into_a_working_grant(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    db_session,
):
    pod_id = await _make_pod(authenticated_client, fixed_test_org["id"])
    file_payload = await _make_file(authenticated_client, pod_id)

    # Someone with no relationship to the pod or the org at all.
    outsider = await signup_user(async_client, "invite-outsider")

    before = await async_client.get(
        f"/pods/{pod_id}/datastore/files/by-path",
        params={"path": file_payload["path"]},
        headers=auth_headers(outsider),
    )
    assert before.status_code != status.HTTP_200_OK

    invited = await authenticated_client.post(
        f"/pods/{pod_id}/resource-access-invites",
        json={
            "resource_type": "document",
            "resource_id": file_payload["id"],
            # Deliberately differently-cased: the address is normalized on the
            # way in so redemption matches on equality.
            "email": outsider["email"].upper(),
            "permission_ids": ["folder.read"],
        },
    )
    assert invited.status_code == status.HTTP_201_CREATED, invited.text

    redeemed = await ResourceAccessInviteService(db_session).redeem_for_user(
        user_id=UUID(outsider["id"]),
        email=outsider["email"],
    )
    await db_session.commit()
    assert redeemed == 1

    after = await async_client.get(
        f"/pods/{pod_id}/datastore/files/by-path",
        params={"path": file_payload["path"]},
        headers=auth_headers(outsider),
    )
    assert after.status_code == status.HTTP_200_OK, after.text

    # Still not a member: the grant is the whole of what changed.
    tree = await async_client.get(
        f"/pods/{pod_id}/datastore/files/tree",
        params={"root_path": "/"},
        headers=auth_headers(outsider),
    )
    assert tree.status_code == status.HTTP_403_FORBIDDEN, tree.text


async def test_invite_rejects_unknown_permissions(
    authenticated_client: AsyncClient, fixed_test_org
):
    # An invite becomes a grant verbatim, so it must not be a way around the
    # validation the grant API applies.
    pod_id = await _make_pod(authenticated_client, fixed_test_org["id"])
    file_payload = await _make_file(authenticated_client, pod_id)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/resource-access-invites",
        json={
            "resource_type": "document",
            "resource_id": file_payload["id"],
            "email": f"future+{uuid4().hex[:8]}@example.com",
            "permission_ids": ["folder.read", "not.a.permission"],
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text
