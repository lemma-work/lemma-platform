from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from starlette import status


def auth_headers(user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


async def signup_user(async_client: AsyncClient, prefix: str) -> dict[str, str]:
    email = f"test+{prefix}-{uuid4().hex[:8]}@example.com"
    response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": "TestPassword@123"},
            ]
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    payload = response.json()
    token = response.headers.get("st-access-token") or response.cookies.get(
        "sAccessToken"
    )
    assert payload.get("status") == "OK", payload
    assert token
    return {"email": email, "token": token, "id": payload["user"]["id"]}


async def invite_org_member(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    user: dict[str, str],
) -> dict:
    invite = await owner_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": user["email"], "role": "ORG_MEMBER"},
    )
    assert invite.status_code == status.HTTP_201_CREATED, invite.text
    accept = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=auth_headers(user),
    )
    assert accept.status_code == status.HTTP_200_OK, accept.text
    members = await owner_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == status.HTTP_200_OK, members.text
    return next(
        item
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == user["email"]
    )


async def add_pod_member(
    owner_client: AsyncClient,
    *,
    pod_id: str,
    organization_member_id: str,
    role: str,
    roles: list[str] | None = None,
) -> dict:
    payload = {"organization_member_id": organization_member_id, "roles": roles or [role]}
    response = await owner_client.post(f"/pods/{pod_id}/members", json=payload)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def create_role_visibility_context(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    *,
    pod_name_prefix: str,
    custom_role: str = "QA_REVIEWERS",
) -> dict:
    pod_response = await owner_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"{pod_name_prefix} {uuid4().hex[:8]}",
            "description": "Role visibility e2e pod",
            "type": "HYBRID",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]
    custom_role = custom_role.upper()

    role_response = await owner_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": custom_role.lower()},
    )
    assert role_response.status_code == status.HTTP_201_CREATED, role_response.text
    assert role_response.json()["name"] == custom_role

    viewer = await signup_user(async_client, f"{pod_name_prefix}-viewer")
    custom_viewer = await signup_user(async_client, f"{pod_name_prefix}-custom")
    editor = await signup_user(async_client, f"{pod_name_prefix}-editor")

    viewer_org_member = await invite_org_member(
        owner_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=viewer,
    )
    custom_org_member = await invite_org_member(
        owner_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=custom_viewer,
    )
    editor_org_member = await invite_org_member(
        owner_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=editor,
    )

    viewer_member = await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=viewer_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER"],
    )
    custom_member = await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=custom_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER", custom_role],
    )
    editor_member = await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=editor_org_member["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR"],
    )

    return {
        "pod_id": pod_id,
        "custom_role": custom_role,
        "viewer": viewer,
        "custom_viewer": custom_viewer,
        "editor": editor,
        "viewer_headers": auth_headers(viewer),
        "custom_headers": auth_headers(custom_viewer),
        "editor_headers": auth_headers(editor),
        "viewer_member": viewer_member,
        "custom_member": custom_member,
        "editor_member": editor_member,
    }


def item_names(payload: dict) -> set[str]:
    return {item["name"] for item in payload["items"]}
