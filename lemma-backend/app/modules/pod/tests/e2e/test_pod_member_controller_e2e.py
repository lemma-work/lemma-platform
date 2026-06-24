from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def _signup_user(async_client: AsyncClient) -> tuple[str, str]:
    email = f"test+pod-member-{uuid4().hex[:10]}@example.com"
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
    data = response.json()
    assert data.get("status") == "OK", data

    token = response.headers.get("st-access-token") or response.cookies.get(
        "sAccessToken"
    )
    assert token
    return email, token


async def _update_profile(
    async_client: AsyncClient,
    token: str,
    *,
    first_name: str,
    last_name: str,
) -> dict:
    response = await async_client.post(
        "/users/me/profile",
        json={"first_name": first_name, "last_name": last_name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_pod_member_lifecycle(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]

    # 1. Create pod as primary user.
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": "Pod Member E2E",
            "organization_id": org_id,
            "description": "pod member lifecycle",
        },
    )
    assert pod_response.status_code == 201, pod_response.text
    pod_id = pod_response.json()["id"]

    # 2. Signup secondary user and invite to org.
    invitee_email, invitee_token = await _signup_user(async_client)
    invitee_profile = await _update_profile(
        async_client,
        invitee_token,
        first_name="Pod",
        last_name="Member",
    )

    invitation_response = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": invitee_email, "role": "ORG_MEMBER"},
    )
    assert invitation_response.status_code == 201, invitation_response.text
    invitation_id = invitation_response.json()["id"]

    # 3. Secondary user accepts invitation.
    accept_response = await async_client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers={"Authorization": f"Bearer {invitee_token}"},
    )
    assert accept_response.status_code == 200, accept_response.text

    # 4. Resolve organization_member_id for invitee.
    org_members_response = await authenticated_client.get(f"/organizations/{org_id}/members")
    assert org_members_response.status_code == 200, org_members_response.text
    members = org_members_response.json().get("items", [])

    invitee_org_member = next(
        (member for member in members if member.get("user", {}).get("email") == invitee_email),
        None,
    )
    assert invitee_org_member is not None

    # 5. Add invitee to pod.
    add_member_response = await authenticated_client.post(
        f"/pods/{pod_id}/members",
        json={
            "organization_member_id": invitee_org_member["id"],
            "roles": ["POD_EDITOR"],
        },
    )
    assert add_member_response.status_code == 201, add_member_response.text
    pod_member = add_member_response.json()
    assert pod_member["pod_member_id"]
    assert pod_member["user_id"] == invitee_org_member["user"]["id"]
    assert pod_member["email"] == invitee_email
    assert pod_member["user_email"] == invitee_email
    assert pod_member["user_name"] == "Pod Member"

    # 6. Verify member list includes invitee.
    pod_members_response = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert pod_members_response.status_code == 200, pod_members_response.text
    pod_members = pod_members_response.json().get("items", [])
    assert any(member["user_id"] == invitee_org_member["user"]["id"] for member in pod_members)
    listed_member = next(
        member for member in pod_members if member["user_id"] == invitee_org_member["user"]["id"]
    )
    assert listed_member["pod_member_id"] == pod_member["pod_member_id"]
    assert listed_member["email"] == invitee_email
    assert listed_member["user_email"] == invitee_email
    assert listed_member["user_name"] == "Pod Member"

    # 7. Fetch member detail by pod member id.
    get_member_response = await authenticated_client.get(
        f"/pods/{pod_id}/members/{pod_member['pod_member_id']}"
    )
    assert get_member_response.status_code == 200, get_member_response.text
    member_detail = get_member_response.json()
    assert member_detail["pod_member_id"] == pod_member["pod_member_id"]
    assert member_detail["user_id"] == invitee_org_member["user"]["id"]
    assert member_detail["email"] == invitee_email
    assert member_detail["user_name"] == "Pod Member"
    assert member_detail["user"]["id"] == invitee_profile["id"]
    assert member_detail["user"]["email"] == invitee_email
    assert member_detail["user"]["first_name"] == "Pod"
    assert member_detail["user"]["last_name"] == "Member"

    lookup_by_user_response = await authenticated_client.get(
        f"/pods/{pod_id}/members/lookup/by-user-id/{invitee_org_member['user']['id']}"
    )
    assert lookup_by_user_response.status_code == 200, lookup_by_user_response.text
    assert lookup_by_user_response.json()["pod_member_id"] == pod_member["pod_member_id"]

    lookup_by_email_response = await authenticated_client.get(
        f"/pods/{pod_id}/members/lookup/by-email",
        params={"email": invitee_email},
    )
    assert lookup_by_email_response.status_code == 200, lookup_by_email_response.text
    assert lookup_by_email_response.json()["user_id"] == invitee_profile["id"]
    assert lookup_by_email_response.json()["pod_member_id"] == pod_member["pod_member_id"]

    missing_lookup_response = await authenticated_client.get(
        f"/pods/{pod_id}/members/lookup/by-email",
        params={"email": "missing@example.com"},
    )
    assert missing_lookup_response.status_code == 404, missing_lookup_response.text

    # 8. Update roles.
    update_role_response = await authenticated_client.patch(
        f"/pods/{pod_id}/members/{pod_member['pod_member_id']}/roles",
        json={"roles": ["POD_VIEWER"]},
    )
    assert update_role_response.status_code == 200, update_role_response.text
    assert update_role_response.json()["roles"] == ["POD_VIEWER"]
    assert update_role_response.json()["user_name"] == "Pod Member"

    # 9. Remove member.
    remove_response = await authenticated_client.delete(
        f"/pods/{pod_id}/members/{pod_member['pod_member_id']}"
    )
    assert remove_response.status_code == 204, remove_response.text

    # 10. Verify member removed.
    pod_members_after_response = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert pod_members_after_response.status_code == 200, pod_members_after_response.text
    pod_members_after = pod_members_after_response.json().get("items", [])
    assert all(
        member["user_id"] != invitee_org_member["user"]["id"]
        for member in pod_members_after
    )

    missing_member_response = await authenticated_client.get(
        f"/pods/{pod_id}/members/{pod_member['pod_member_id']}"
    )
    assert missing_member_response.status_code == 404, missing_member_response.text


@pytest.mark.asyncio
async def test_add_member_rejects_cross_org_member(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    primary_org_id = fixed_test_org["id"]

    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "name": "Cross Org Pod Member Validation",
            "organization_id": primary_org_id,
            "description": "cross org validation",
        },
    )
    assert pod_response.status_code == 201, pod_response.text
    pod_id = pod_response.json()["id"]

    add_cross_org_member = await authenticated_client.post(
        f"/pods/{pod_id}/members",
        json={
            "organization_member_id": str(uuid4()),
            "roles": ["POD_VIEWER"],
        },
    )
    assert add_cross_org_member.status_code == 400, add_cross_org_member.text


@pytest.mark.asyncio
async def test_update_remove_member_rejects_mismatched_pod_path(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]

    pod_a_response = await authenticated_client.post(
        "/pods",
        json={
            "name": "Path Validation Pod A",
            "organization_id": org_id,
            "description": "pod a",
        },
    )
    assert pod_a_response.status_code == 201, pod_a_response.text
    pod_a_id = pod_a_response.json()["id"]

    pod_b_response = await authenticated_client.post(
        "/pods",
        json={
            "name": "Path Validation Pod B",
            "organization_id": org_id,
            "description": "pod b",
        },
    )
    assert pod_b_response.status_code == 201, pod_b_response.text
    pod_b_id = pod_b_response.json()["id"]

    invitee_email, invitee_token = await _signup_user(async_client)
    invitation_response = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": invitee_email, "role": "ORG_MEMBER"},
    )
    assert invitation_response.status_code == 201, invitation_response.text
    invitation_id = invitation_response.json()["id"]

    accept_response = await async_client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers={"Authorization": f"Bearer {invitee_token}"},
    )
    assert accept_response.status_code == 200, accept_response.text

    org_members_response = await authenticated_client.get(f"/organizations/{org_id}/members")
    assert org_members_response.status_code == 200, org_members_response.text
    invitee_org_member = next(
        (
            member
            for member in org_members_response.json().get("items", [])
            if member.get("user", {}).get("email") == invitee_email
        ),
        None,
    )
    assert invitee_org_member is not None

    add_member_response = await authenticated_client.post(
        f"/pods/{pod_a_id}/members",
        json={
            "organization_member_id": invitee_org_member["id"],
            "roles": ["POD_EDITOR"],
        },
    )
    assert add_member_response.status_code == 201, add_member_response.text
    pod_member_id = add_member_response.json()["pod_member_id"]

    update_wrong_path = await authenticated_client.patch(
        f"/pods/{pod_b_id}/members/{pod_member_id}/roles",
        json={"roles": ["POD_VIEWER"]},
    )
    assert update_wrong_path.status_code == 404, update_wrong_path.text

    delete_wrong_path = await authenticated_client.delete(
        f"/pods/{pod_b_id}/members/{pod_member_id}",
    )
    assert delete_wrong_path.status_code == 404, delete_wrong_path.text
