"""Renaming a Lemma: PATCH /pods/{pod_id} is a partial update.

PATCH used to answer 405 while PUT -- itself always partial -- worked, so a
client following the rest of the API (every other update is a PATCH) could not
rename a Lemma at all.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.modules.test_support.e2e_authz import signup_user

pytestmark = pytest.mark.e2e


async def _create_pod(client: AsyncClient, org_id: str) -> dict[str, object]:
    response = await client.post(
        "/pods",
        json={
            "name": f"Rename Me {uuid4().hex[:8]}",
            "organization_id": org_id,
            "description": "kept across a rename",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_patch_renames_and_keeps_every_field_not_sent(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod = await _create_pod(authenticated_client, fixed_test_org["id"])
    new_name = f"Renamed {uuid4().hex[:8]}"

    response = await authenticated_client.patch(
        f"/pods/{pod['id']}", json={"name": new_name}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == new_name
    assert body["description"] == "kept across a rename"
    assert body["config"] == pod["config"]
    stored = await authenticated_client.get(f"/pods/{pod['id']}")
    assert stored.json()["name"] == new_name


@pytest.mark.asyncio
async def test_patch_refuses_a_name_another_lemma_in_the_org_has(
    authenticated_client: AsyncClient, fixed_test_org
):
    first = await _create_pod(authenticated_client, fixed_test_org["id"])
    second = await _create_pod(authenticated_client, fixed_test_org["id"])

    response = await authenticated_client.patch(
        f"/pods/{second['id']}", json={"name": first["name"]}
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "POD_CONFLICT"


@pytest.mark.asyncio
async def test_put_still_updates_for_clients_built_against_it(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod = await _create_pod(authenticated_client, fixed_test_org["id"])

    response = await authenticated_client.put(
        f"/pods/{pod['id']}", json={"description": "changed by PUT"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == pod["name"]
    assert response.json()["description"] == "changed by PUT"


@pytest.mark.asyncio
async def test_patch_is_refused_to_someone_outside_the_lemma(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    pod = await _create_pod(authenticated_client, fixed_test_org["id"])
    outsider = await signup_user(async_client, f"pod-rename-{uuid4().hex[:10]}")

    response = await async_client.patch(
        f"/pods/{pod['id']}",
        json={"name": f"Hijacked {uuid4().hex[:8]}"},
        headers={"Authorization": f"Bearer {outsider['token']}"},
    )

    assert response.status_code == 403, response.text
    stored = await authenticated_client.get(f"/pods/{pod['id']}")
    assert stored.json()["name"] == pod["name"]
