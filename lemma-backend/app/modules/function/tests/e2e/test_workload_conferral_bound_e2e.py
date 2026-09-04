"""Granting a function access is conferral too (PS-ACCESS-010).

A function runs on its own grants, so granting it a permission the granter does
not hold is a way to do through the function what you may not do yourself. The
agent route is the same rule, covered in the agent module.

Marked ``workspace`` and taking ``configure_workspace_api_url``: creating a
function derives its schemas in a real sandbox, so this needs both the image and
the fixture that points the runtime at it — without the fixture the create fails
with `WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes`.

The actor holds ``function.update`` through a custom role and deliberately does
**not** hold ``datastore.table.delete``.
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

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]


async def _create_pod(owner_client: AsyncClient, org_id: str) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Workload Conferral {uuid4().hex[:8]}",
            "description": "workload conferral bound e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_role(
    owner_client: AsyncClient, *, pod_id: str, name: str, permission_ids: list[str]
) -> None:
    response = await owner_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": name, "permission_ids": permission_ids},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _member_holding(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    prefix: str,
    roles: list[str],
) -> dict:
    user = await signup_user(async_client, prefix)
    org_member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role=roles[0],
        roles=roles,
    )
    return user


async def _table(owner_client: AsyncClient, pod_id: str) -> str:
    name = f"ledger_{uuid4().hex[:8]}"
    response = await owner_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "description": "conferral target",
            "columns": [{"name": "note", "type": "TEXT"}],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return name


def _grants(table_name: str, permission_ids: list[str]) -> dict:
    return {
        "grants": [
            {
                "resource_type": "datastore_table",
                "resource_name": table_name,
                "permission_ids": permission_ids,
            }
        ]
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_a_function_cannot_be_granted_what_the_granter_lacks(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    pod_id = await _create_pod(authenticated_client, org_id)
    table_name = await _table(authenticated_client, pod_id)

    await _create_role(
        authenticated_client,
        pod_id=pod_id,
        name="function_wrangler",
        permission_ids=[
            "function.create",
            "function.update",
            "function.read",
            "datastore.record.read",
        ],
    )
    actor = await _member_holding(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        prefix="function-conferral",
        roles=["POD_VIEWER", "FUNCTION_WRANGLER"],
    )

    function_name = f"scorer_{uuid4().hex[:6]}"
    code = f"""#input_type_name: ScoreInput
#output_type_name: ScoreResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class ScoreInput(BaseModel):
    note: str

class ScoreResult(BaseModel):
    score: int

async def {function_name}(ctx: FunctionContext, data: ScoreInput) -> ScoreResult:
    return ScoreResult(score=len(data.note))"""
    created = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": function_name,
            "description": "conferral target",
            "type": "API",
            "code": code,
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text

    refused = await async_client.put(
        f"/pods/{pod_id}/functions/{function_name}/permissions",
        json=_grants(table_name, ["datastore.table.delete"]),
        headers=auth_headers(actor),
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER", refused.text

    allowed = await async_client.put(
        f"/pods/{pod_id}/functions/{function_name}/permissions",
        json=_grants(table_name, ["datastore.record.read"]),
        headers=auth_headers(actor),
    )
    assert allowed.status_code == status.HTTP_200_OK, allowed.text
