"""The conferral bound over the real HTTP surface (PS-POD-013, PS-ACCESS-010).

Nobody hands out access they do not themselves hold. Three routes could,
and each one needs an actor who genuinely reaches the check rather than one
who is refused earlier for an unrelated reason:

- ``PATCH /pods/{id}/members/{member_id}/roles`` is gated on
  ``pod.member.manage``, which POD_EDITOR does not carry -- so the escalating
  actor here holds POD_EDITOR *and* a custom role that carries it. A plain
  editor never gets far enough to test the bound at all, which is how the
  hierarchy-rank version of it survived.
- ``POST``/``PATCH /pods/{id}/roles`` and the resource-grant replace are gated
  on ``pod.role.manage``, likewise reached through a custom role.

Each test also asserts the actor can still confer what they *do* hold, because
a bound that refuses everything would pass the refusal half on its own.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

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
            "description": "conferral bound e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_role(
    owner_client: AsyncClient,
    *,
    pod_id: str,
    name: str,
    permission_ids: list[str],
) -> dict:
    response = await owner_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": name, "permission_ids": permission_ids},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _pod_member(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    prefix: str,
    roles: list[str],
) -> tuple[dict, dict]:
    user = await signup_user(async_client, prefix)
    org_member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    member = await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role=roles[0],
        roles=roles,
    )
    return user, member


@pytest.mark.asyncio
async def test_member_manager_cannot_assign_a_role_beyond_their_permissions(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """ACCESS-01: a custom role scored 0 against ``ROLE_HIERARCHY`` and cleared
    the editor cap, so an editor who could manage members could hand somebody an
    admin-equivalent role."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Conferral Pod")

    # What the actor holds: member management, and nothing else above editor.
    await _create_role(
        authenticated_client,
        pod_id=pod_id,
        name="member_wrangler",
        permission_ids=["pod.member.manage"],
    )
    # What they must not be able to confer: administration by another name.
    await _create_role(
        authenticated_client,
        pod_id=pod_id,
        name="custodian",
        permission_ids=[
            "pod.member.manage",
            "pod.role.manage",
            "datastore.table.delete",
        ],
    )

    actor, _ = await _pod_member(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        prefix="conferral-editor",
        roles=["POD_EDITOR", "MEMBER_WRANGLER"],
    )
    _, target = await _pod_member(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        prefix="conferral-target",
        roles=["POD_VIEWER"],
    )
    target_member_id = target["pod_member_id"]

    escalate = await async_client.patch(
        f"/pods/{pod_id}/members/{target_member_id}/roles",
        json={"roles": ["CUSTODIAN"]},
        headers=auth_headers(actor),
    )
    assert escalate.status_code == status.HTTP_403_FORBIDDEN, escalate.text
    # The refusal names itself, so a client can tell "you may not grant this"
    # apart from "you may not be here at all".
    assert escalate.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER", escalate.text

    # The built-in ladder is refused by the same one check, not a second one.
    promote = await async_client.patch(
        f"/pods/{pod_id}/members/{target_member_id}/roles",
        json={"roles": ["POD_ADMIN"]},
        headers=auth_headers(actor),
    )
    assert promote.status_code == status.HTTP_403_FORBIDDEN, promote.text

    # ... and the actor can still confer exactly what they hold.
    allowed = await async_client.patch(
        f"/pods/{pod_id}/members/{target_member_id}/roles",
        json={"roles": ["POD_USER", "MEMBER_WRANGLER"]},
        headers=auth_headers(actor),
    )
    assert allowed.status_code == status.HTTP_200_OK, allowed.text
    assert set(allowed.json()["roles"]) == {"POD_USER", "MEMBER_WRANGLER"}


@pytest.mark.asyncio
async def test_role_author_cannot_put_permissions_they_lack_into_a_role(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """ACCESS-02: ``create_or_update_role`` validated that a permission id
    exists, never that its author holds it."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Role Author Pod")

    await _create_role(
        authenticated_client,
        pod_id=pod_id,
        name="role_curator",
        permission_ids=["pod.role.manage"],
    )
    author, _ = await _pod_member(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        prefix="conferral-author",
        roles=["POD_USER", "ROLE_CURATOR"],
    )

    minted = await async_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "shadow_admin",
            "permission_ids": ["pod.member.manage", "datastore.table.delete"],
        },
        headers=auth_headers(author),
    )
    assert minted.status_code == status.HTTP_403_FORBIDDEN, minted.text
    assert minted.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER", minted.text

    # The role must not exist as a side effect of the refused request.
    roles = await authenticated_client.get(f"/pods/{pod_id}/roles")
    assert roles.status_code == status.HTTP_200_OK, roles.text
    assert "SHADOW_ADMIN" not in {item["name"] for item in roles.json()["items"]}

    within_bounds = await async_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": "reader", "permission_ids": ["pod.read", "agent.execute"]},
        headers=auth_headers(author),
    )
    assert within_bounds.status_code == status.HTTP_201_CREATED, within_bounds.text

    # Updating an existing role is the same door, and was open too.
    widened = await async_client.patch(
        f"/pods/{pod_id}/roles/READER",
        json={"name": "reader", "permission_ids": ["pod.read", "pod.member.manage"]},
        headers=auth_headers(author),
    )
    assert widened.status_code == status.HTTP_403_FORBIDDEN, widened.text

    permissions = await authenticated_client.get(f"/pods/{pod_id}/roles")
    reader = next(
        item for item in permissions.json()["items"] if item["name"] == "READER"
    )
    assert "pod.member.manage" not in reader["permission_ids"]


@pytest.mark.asyncio
async def test_resource_grant_cannot_confer_a_permission_the_sharer_lacks(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """ACCESS-02: the resource-grant replace was gated on ``pod.role.manage``
    alone, so it could hand out a permission its caller did not hold."""
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id, "Resource Grant Pod")

    table = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": f"ledger_{uuid4().hex[:8]}",
            "columns": [{"name": "amount", "type": "TEXT"}],
        },
    )
    assert table.status_code == status.HTTP_201_CREATED, table.text
    table_name = table.json()["name"]

    await _create_role(
        authenticated_client,
        pod_id=pod_id,
        name="sharer",
        permission_ids=["pod.role.manage", "datastore.record.read"],
    )
    sharer, _ = await _pod_member(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        prefix="conferral-sharer",
        roles=["POD_VIEWER", "SHARER"],
    )
    _, grantee = await _pod_member(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        prefix="conferral-grantee",
        roles=["POD_VIEWER"],
    )

    grant_path = (
        f"/pods/{pod_id}/resources/datastore_table/{table_name}"
        f"/access/grantees/POD_MEMBER/{grantee['pod_member_id']}"
    )
    beyond = await async_client.put(
        grant_path,
        json={"permission_ids": ["datastore.record.write"]},
        headers=auth_headers(sharer),
    )
    assert beyond.status_code == status.HTTP_403_FORBIDDEN, beyond.text
    assert beyond.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER", beyond.text

    within = await async_client.put(
        grant_path,
        json={"permission_ids": ["datastore.record.read"]},
        headers=auth_headers(sharer),
    )
    assert within.status_code == status.HTTP_200_OK, within.text
