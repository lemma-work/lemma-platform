import pytest
from httpx import AsyncClient
from uuid import uuid4

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client: AsyncClient, org_id: str, *, name: str) -> dict:
    payload = {
        "name": name,
        "organization_id": org_id,
        "description": "Created via E2E test",
    }
    response = await authenticated_client.post(
        "/pods", json=payload, follow_redirects=True
    )
    assert response.status_code == 201, f"Pod creation failed: {response.text}"
    return response.json()


@pytest.mark.asyncio
async def test_pod_workflow(authenticated_client, fixed_test_org):
    org_id = fixed_test_org["id"]
    pod_data = await _create_pod(
        authenticated_client,
        org_id,
        name="E2E Test Pod",
    )
    assert pod_data["name"] == "E2E Test Pod"
    pod_id = pod_data["id"]

    # 3. List Pods
    # Note: API requires organization ID to list pods
    response = await authenticated_client.get(
        f"/pods/organization/{org_id}", follow_redirects=True
    )

    assert response.status_code == 200
    pods = response.json()

    # API likely returns {"items": [...], "total": ...}
    if "items" in pods:
        items = pods["items"]
    else:
        items = pods

    found = any(p["id"] == pod_id for p in items)
    assert found

    # 4. Get Pod Detail
    response = await authenticated_client.get(f"/pods/{pod_id}", follow_redirects=True)
    assert response.status_code == 200
    assert response.json()["id"] == pod_id

    # 5. Update Pod
    update_payload = {
        "name": "E2E Updated Pod",
        "description": "Updated description",
    }
    response = await authenticated_client.put(
        f"/pods/{pod_id}", json=update_payload, follow_redirects=True
    )
    assert response.status_code == 200
    assert response.json()["name"] == "E2E Updated Pod"

    # 6. List Members (Creator should be there)
    response = await authenticated_client.get(
        f"/pods/{pod_id}/members", follow_redirects=True
    )
    assert response.status_code == 200
    members = response.json()

    if "items" in members:
        member_items = members["items"]
    else:
        member_items = members

    assert len(member_items) >= 1

    # 7. Delete Pod
    response = await authenticated_client.delete(f"/pods/{pod_id}", follow_redirects=True)
    assert response.status_code == 204

    # 8. Verify deleted pods are no longer visible
    response = await authenticated_client.get(f"/pods/{pod_id}", follow_redirects=True)
    assert response.status_code == 404

    response = await authenticated_client.get(
        f"/pods/organization/{org_id}",
        follow_redirects=True,
    )
    assert response.status_code == 200
    items = response.json().get("items", [])
    assert all(item["id"] != pod_id for item in items)


@pytest.mark.asyncio
async def test_list_pods_by_organization(authenticated_client, fixed_test_org):
    org_id = fixed_test_org["id"]
    pod = await _create_pod(authenticated_client, org_id, name="E2E List Pod")

    response = await authenticated_client.get(
        f"/pods/organization/{org_id}",
        follow_redirects=True,
    )

    assert response.status_code == 200
    data = response.json()
    items = data.get("items", [])
    assert any(item["id"] == pod["id"] for item in items)


@pytest.mark.asyncio
async def test_list_pods_by_organization_uses_last_returned_id_as_next_page_token(
    authenticated_client,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    await _create_pod(authenticated_client, org_id, name="Cursor Pod A")
    await _create_pod(authenticated_client, org_id, name="Cursor Pod B")
    await _create_pod(authenticated_client, org_id, name="Cursor Pod C")

    first_page = await authenticated_client.get(
        f"/pods/organization/{org_id}",
        params={"limit": 2},
        follow_redirects=True,
    )

    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert len(first_payload["items"]) == 2
    assert first_payload["next_page_token"] == first_payload["items"][-1]["id"]

    second_page = await authenticated_client.get(
        f"/pods/organization/{org_id}",
        params={"limit": 2, "page_token": first_payload["next_page_token"]},
        follow_redirects=True,
    )

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert all(
        item["id"] > first_payload["next_page_token"]
        for item in second_payload["items"]
    )


async def _add_org_member(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    org_id: str,
    *,
    org_role: str = "ORG_MEMBER",
) -> tuple[str, str]:
    """Create a second user, invite + accept into the org.

    Returns ``(bearer_token, organization_member_id)``.
    """
    email = f"test+pod-del-{uuid4().hex[:10]}@example.com"
    password = "TestPassword@123"
    signup = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": password},
            ]
        },
    )
    assert signup.status_code == 200, signup.text
    token = signup.headers.get("st-access-token") or signup.cookies.get("sAccessToken")
    assert token

    invite = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": email, "role": org_role},
    )
    assert invite.status_code == 201, invite.text
    accept = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert accept.status_code == 200, accept.text

    members = await authenticated_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == 200, members.text
    org_member_id = next(
        m["id"]
        for m in members.json()["items"]
        if (m.get("user") or {}).get("email") == email
    )
    return token, org_member_id


async def _assert_pod_fully_gone(client: AsyncClient, org_id: str, pod_id: str, **kw):
    """A soft-deleted pod must not surface anywhere and re-deleting yields 404."""
    detail = await client.get(f"/pods/{pod_id}", follow_redirects=True, **kw)
    assert detail.status_code == 404, detail.text

    listing = await client.get(
        f"/pods/organization/{org_id}", follow_redirects=True, **kw
    )
    assert listing.status_code == 200, listing.text
    assert all(item["id"] != pod_id for item in listing.json().get("items", []))

    redelete = await client.delete(f"/pods/{pod_id}", follow_redirects=True, **kw)
    assert redelete.status_code == 404, redelete.text


@pytest.mark.asyncio
async def test_pod_admin_member_can_delete_and_pod_disappears(
    authenticated_client, async_client, fixed_test_org
):
    """A non-owner POD_ADMIN can delete; afterwards the pod is gone everywhere."""
    org_id = fixed_test_org["id"]
    pod = await _create_pod(authenticated_client, org_id, name="Admin Delete Pod")
    pod_id = pod["id"]

    token, org_member_id = await _add_org_member(
        authenticated_client, async_client, org_id
    )
    add = await authenticated_client.post(
        f"/pods/{pod_id}/members",
        json={"organization_member_id": org_member_id, "roles": ["POD_ADMIN"]},
    )
    assert add.status_code == 201, add.text

    headers = {"Authorization": f"Bearer {token}"}
    deleted = await async_client.delete(
        f"/pods/{pod_id}", headers=headers, follow_redirects=True
    )
    assert deleted.status_code == 204, deleted.text

    await _assert_pod_fully_gone(async_client, org_id, pod_id, headers=headers)


@pytest.mark.asyncio
async def test_org_owner_can_delete_pod_they_do_not_belong_to(
    authenticated_client, async_client, fixed_test_org
):
    """Regression: org owners see every pod, so they must be able to delete them.

    Previously the ``pod.delete`` dependency guard returned 403 for org owners who
    were not pod members, even though the service grants them delete rights.
    """
    org_id = fixed_test_org["id"]

    # A different org member creates the pod (and becomes its only admin member).
    token, _ = await _add_org_member(authenticated_client, async_client, org_id)
    create = await async_client.post(
        "/pods",
        json={"name": "Owner Deletes This", "organization_id": org_id},
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=True,
    )
    assert create.status_code == 201, create.text
    pod_id = create.json()["id"]

    # The org owner sees it in the list and can delete it.
    listing = await authenticated_client.get(
        f"/pods/organization/{org_id}", follow_redirects=True
    )
    assert any(item["id"] == pod_id for item in listing.json().get("items", []))

    deleted = await authenticated_client.delete(
        f"/pods/{pod_id}", follow_redirects=True
    )
    assert deleted.status_code == 204, deleted.text

    await _assert_pod_fully_gone(authenticated_client, org_id, pod_id)


@pytest.mark.asyncio
async def test_non_admin_pod_member_cannot_delete_pod(
    authenticated_client, async_client, fixed_test_org
):
    """A pod member without admin rights must not be able to delete the pod."""
    org_id = fixed_test_org["id"]
    pod = await _create_pod(authenticated_client, org_id, name="Protected Pod")
    pod_id = pod["id"]

    token, org_member_id = await _add_org_member(
        authenticated_client, async_client, org_id
    )
    add = await authenticated_client.post(
        f"/pods/{pod_id}/members",
        json={"organization_member_id": org_member_id, "roles": ["POD_USER"]},
    )
    assert add.status_code == 201, add.text

    headers = {"Authorization": f"Bearer {token}"}
    resp = await async_client.delete(
        f"/pods/{pod_id}", headers=headers, follow_redirects=True
    )
    assert resp.status_code == 403, resp.text

    # The pod is untouched and still visible to the owner.
    detail = await authenticated_client.get(f"/pods/{pod_id}", follow_redirects=True)
    assert detail.status_code == 200


@pytest.mark.asyncio
async def test_deleted_pod_name_can_be_reused(authenticated_client, fixed_test_org):
    org_id = fixed_test_org["id"]
    first_pod = await _create_pod(authenticated_client, org_id, name="Reusable Pod")

    response = await authenticated_client.delete(
        f"/pods/{first_pod['id']}",
        follow_redirects=True,
    )
    assert response.status_code == 204

    second_pod = await _create_pod(authenticated_client, org_id, name="Reusable Pod")

    assert second_pod["id"] != first_pod["id"]

    deleted_pod_response = await authenticated_client.get(
        f"/pods/organization/{org_id}",
        follow_redirects=True,
    )
    assert deleted_pod_response.status_code == 200
    active_ids = {item["id"] for item in deleted_pod_response.json().get("items", [])}
    assert first_pod["id"] not in active_ids


@pytest.mark.asyncio
async def test_create_pod_rejects_duplicate_name_in_same_organization(
    authenticated_client,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    pod_name = f"Unique Pod {uuid4().hex[:8]}"
    await _create_pod(authenticated_client, org_id, name=pod_name)

    duplicate = await authenticated_client.post(
        "/pods",
        json={
            "name": pod_name,
            "organization_id": org_id,
            "description": "duplicate should fail",
        },
        follow_redirects=True,
    )

    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "POD_CONFLICT"


@pytest.mark.asyncio
async def test_update_pod_rejects_duplicate_name_in_same_organization(
    authenticated_client,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    first_name = f"Update Unique Pod {uuid4().hex[:8]}"
    second_name = f"Update Duplicate Pod {uuid4().hex[:8]}"
    await _create_pod(authenticated_client, org_id, name=first_name)
    second = await _create_pod(authenticated_client, org_id, name=second_name)

    duplicate = await authenticated_client.put(
        f"/pods/{second['id']}",
        json={"name": first_name},
        follow_redirects=True,
    )

    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "POD_CONFLICT"


@pytest.mark.asyncio
async def test_list_pods_by_organization_only_returns_member_pods(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    pod = await _create_pod(authenticated_client, org_id, name="Visible To Creator")

    outsider_email = f"test+pod-list-{uuid4().hex[:10]}@example.com"
    password = "TestPassword@123"
    signup_response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": outsider_email},
                {"id": "password", "value": password},
            ]
        },
    )
    assert signup_response.status_code == 200, signup_response.text
    outsider_token = signup_response.headers.get("st-access-token") or signup_response.cookies.get(
        "sAccessToken"
    )
    assert outsider_token

    invite_response = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": outsider_email, "role": "ORG_MEMBER"},
    )
    assert invite_response.status_code == 201, invite_response.text
    invite_id = invite_response.json()["id"]

    accept_response = await async_client.post(
        f"/organizations/invitations/{invite_id}/accept",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert accept_response.status_code == 200, accept_response.text

    response = await async_client.get(
        f"/pods/organization/{org_id}",
        headers={"Authorization": f"Bearer {outsider_token}"},
        follow_redirects=True,
    )
    assert response.status_code == 200, response.text
    items = response.json().get("items", [])
    assert all(item["id"] != pod["id"] for item in items)
