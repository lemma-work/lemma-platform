"""An inline ``permissions`` block means the same thing on all four verbs.

It did not. ``CreateAgentRequest`` carried the field and the controller applied
it; ``UpdateAgentRequest`` had no such field, so the block was accepted by the
JSON body and dropped. Functions had it on neither. Which of the four calls you
made decided whether your grants existed:

    agent  create → applied      agent  update → silently dropped
    function create → dropped    function update → silently dropped

So an author could create an agent with its grants in one request and lose them
on the next edit, and the identical payload wired an agent while leaving a
function with no access at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

pytestmark = pytest.mark.e2e



async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"grant-parity-{uuid4().hex[:8]}",
            "type": "HYBRID",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_table(authenticated_client, pod_id: str, name: str) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


def _grants(table_name: str) -> dict:
    return {
        "grants": [
            {
                "resource_type": "datastore_table",
                "resource_name": table_name,
                "permission_ids": ["datastore.table.read"],
            }
        ]
    }


async def _granted_tables(authenticated_client, pod_id: str, kind: str, name: str):
    response = await authenticated_client.get(
        f"/pods/{pod_id}/{kind}s/{name}/permissions"
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return {
        grant["resource_name"] for grant in response.json().get("grants") or []
    }


@pytest.mark.asyncio
async def test_an_agent_keeps_its_inline_grants_across_an_update(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_table(authenticated_client, pod_id, "alpha")
    await _create_table(authenticated_client, pod_id, "beta")
    name = f"agent_{uuid4().hex[:8]}"

    created = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "go", "permissions": _grants("alpha")},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert await _granted_tables(authenticated_client, pod_id, "agent", name) == {"alpha"}

    # The regression: this block used to be dropped, leaving `alpha` in place
    # and the author believing they had moved the grant.
    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/agents/{name}",
        json={"instruction": "go on", "permissions": _grants("beta")},
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert await _granted_tables(authenticated_client, pod_id, "agent", name) == {"beta"}


@pytest.mark.asyncio
async def test_a_function_takes_inline_grants_on_create_and_update(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_table(authenticated_client, pod_id, "alpha")
    await _create_table(authenticated_client, pod_id, "beta")
    name = f"fn_{uuid4().hex[:8]}"

    created = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        # No `code`: schema extraction would need a sandbox, and what is under
        # test is where the grants land, not how a body compiles.
        json={"name": name, "permissions": _grants("alpha")},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert await _granted_tables(authenticated_client, pod_id, "function", name) == {
        "alpha"
    }

    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/functions/{name}",
        json={"description": "edited", "permissions": _grants("beta")},
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert await _granted_tables(authenticated_client, pod_id, "function", name) == {
        "beta"
    }


@pytest.mark.asyncio
async def test_omitting_the_block_leaves_existing_grants_alone(
    authenticated_client, fixed_test_org
):
    """The other half of "replace": absent means untouched, which is what makes
    it safe to send an update that is only about the instruction."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_table(authenticated_client, pod_id, "alpha")
    name = f"agent_{uuid4().hex[:8]}"

    await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "go", "permissions": _grants("alpha")},
    )
    await authenticated_client.patch(
        f"/pods/{pod_id}/agents/{name}", json={"instruction": "unrelated edit"}
    )

    assert await _granted_tables(authenticated_client, pod_id, "agent", name) == {"alpha"}
