from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def _signup_user(async_client: AsyncClient) -> tuple[dict, str]:
    email = f"test+pod-join-{uuid4().hex[:10]}@example.com"
    password = "TestPassword@123"

    response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": password},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("status") == "OK", payload

    token = response.headers.get("st-access-token") or response.cookies.get("sAccessToken")
    assert token
    return payload["user"], token


@pytest.mark.asyncio
async def test_pod_join_request_lifecycle(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": "Join Request Pod",
            "organization_id": org_id,
            "description": "join request flow",
        },
    )
    assert pod_response.status_code == 201, pod_response.text
    pod = pod_response.json()

    outsider_user, outsider_token = await _signup_user(async_client)
    outsider_id = outsider_user["id"]

    self_member_before = await async_client.get(
        f"/pods/{pod['id']}/members/{outsider_id}",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert self_member_before.status_code == 403, self_member_before.text
    assert self_member_before.json()["code"] == "POD_ACCESS_DENIED"

    create_request_response = await async_client.post(
        f"/pods/{pod['id']}/join-requests",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert create_request_response.status_code == 201, create_request_response.text
    join_request = create_request_response.json()
    assert join_request["status"] == "PENDING"

    my_request_response = await async_client.get(
        f"/pods/{pod['id']}/join-requests/me",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert my_request_response.status_code == 200, my_request_response.text
    assert my_request_response.json()["id"] == join_request["id"]

    list_requests_response = await authenticated_client.get(
        f"/pods/{pod['id']}/join-requests",
    )
    assert list_requests_response.status_code == 200, list_requests_response.text
    requests = list_requests_response.json().get("items", [])
    listed = next((item for item in requests if item["id"] == join_request["id"]), None)
    assert listed is not None
    # The list surfaces the requesting user so admins know who is asking.
    assert listed["user_email"]
    assert listed["user_email"].endswith("@example.com")
    assert "test+pod-join-" in listed["user_email"]

    approve_response = await authenticated_client.post(
        f"/pods/{pod['id']}/join-requests/{join_request['id']}/approve",
        json={"org_role": "ORG_MEMBER", "pod_role": "POD_VIEWER"},
    )
    assert approve_response.status_code == 200, approve_response.text
    assert approve_response.json()["status"] == "APPROVED"

    pod_members_response = await authenticated_client.get(
        f"/pods/{pod['id']}/members",
    )
    assert pod_members_response.status_code == 200, pod_members_response.text
    approved_member = next(
        (
            member
            for member in pod_members_response.json().get("items", [])
            if member["user_id"] == outsider_id
        ),
        None,
    )
    assert approved_member is not None
    assert approved_member["roles"] == ["POD_VIEWER"]

    self_member_after = await async_client.get(
        f"/pods/{pod['id']}/members/{approved_member['pod_member_id']}",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert self_member_after.status_code == 200, self_member_after.text
    assert self_member_after.json()["user_id"] == outsider_id

    visible_pods_response = await async_client.get(
        f"/pods/organization/{org_id}",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert visible_pods_response.status_code == 200, visible_pods_response.text
    assert any(item["id"] == pod["id"] for item in visible_pods_response.json().get("items", []))


@pytest.mark.asyncio
async def test_pod_self_join_policies(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]

    invite_only = await authenticated_client.post(
        "/pods",
        json={"name": f"InviteOnly {uuid4().hex[:6]}", "organization_id": org_id},
    )
    assert invite_only.status_code == 201, invite_only.text
    invite_only_pod = invite_only.json()
    assert invite_only_pod["config"]["join_policy"] == "INVITE_ONLY"

    public = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Public {uuid4().hex[:6]}",
            "organization_id": org_id,
            "config": {"join_policy": "PUBLIC"},
        },
    )
    assert public.status_code == 201, public.text
    public_pod = public.json()
    assert public_pod["config"]["join_policy"] == "PUBLIC"

    org_members = await authenticated_client.post(
        "/pods",
        json={
            "name": f"OrgMembers {uuid4().hex[:6]}",
            "organization_id": org_id,
            "config": {"join_policy": "ORG_MEMBERS"},
        },
    )
    assert org_members.status_code == 201, org_members.text
    org_members_pod = org_members.json()

    outsider_user, outsider_token = await _signup_user(async_client)
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}

    # Invite-only pod cannot be self-joined.
    invite_only_join = await async_client.post(
        f"/pods/{invite_only_pod['id']}/join", headers=outsider_headers
    )
    assert invite_only_join.status_code == 403

    # Not yet an org member -> cannot join the org-members pod.
    org_members_denied = await async_client.post(
        f"/pods/{org_members_pod['id']}/join", headers=outsider_headers
    )
    assert org_members_denied.status_code == 403

    # Public pod: a non-member self-joins and is auto-added to the org.
    public_join = await async_client.post(
        f"/pods/{public_pod['id']}/join", headers=outsider_headers
    )
    assert public_join.status_code == 200, public_join.text
    assert public_join.json()["user_id"] == outsider_user["id"]
    assert public_join.json()["roles"] == ["POD_USER"]

    # Now an org member, they can self-join the org-members pod.
    org_members_join = await async_client.post(
        f"/pods/{org_members_pod['id']}/join", headers=outsider_headers
    )
    assert org_members_join.status_code == 200, org_members_join.text
    assert org_members_join.json()["roles"] == ["POD_USER"]

    # Joining again is idempotent.
    rejoin = await async_client.post(
        f"/pods/{public_pod['id']}/join", headers=outsider_headers
    )
    assert rejoin.status_code == 200
    assert rejoin.json()["user_id"] == outsider_user["id"]


@pytest.mark.asyncio
async def test_request_join_auto_approves_when_policy_allows(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """The join-requests endpoint should self-join (not park in PENDING) when
    the pod's join policy already entitles the requester."""
    org_id = fixed_test_org["id"]

    invite_only = await authenticated_client.post(
        "/pods",
        json={"name": f"InviteOnly {uuid4().hex[:6]}", "organization_id": org_id},
    )
    assert invite_only.status_code == 201, invite_only.text
    invite_only_pod = invite_only.json()

    public = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Public {uuid4().hex[:6]}",
            "organization_id": org_id,
            "config": {"join_policy": "PUBLIC"},
        },
    )
    assert public.status_code == 201, public.text
    public_pod = public.json()

    outsider_user, outsider_token = await _signup_user(async_client)
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}

    # Invite-only pod: requesting still parks the user in a PENDING request.
    invite_only_request = await async_client.post(
        f"/pods/{invite_only_pod['id']}/join-requests", headers=outsider_headers
    )
    assert invite_only_request.status_code == 201, invite_only_request.text
    assert invite_only_request.json()["status"] == "PENDING"

    # Public pod: requesting auto-approves and grants membership immediately.
    public_request = await async_client.post(
        f"/pods/{public_pod['id']}/join-requests", headers=outsider_headers
    )
    assert public_request.status_code == 201, public_request.text
    assert public_request.json()["status"] == "APPROVED"

    # The user is now a real pod member (auto-added to the org too).
    visible_pods = await async_client.get(
        f"/pods/organization/{org_id}", headers=outsider_headers
    )
    assert visible_pods.status_code == 200, visible_pods.text
    assert any(
        item["id"] == public_pod["id"]
        for item in visible_pods.json().get("items", [])
    )
