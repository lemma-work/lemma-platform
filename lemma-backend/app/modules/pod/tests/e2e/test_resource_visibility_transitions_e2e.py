from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.test_support.e2e_authz import create_role_visibility_context

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_all_resource_apis_allow_visibility_transitions_and_clear_grants(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    scheduler_api_server,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="visibility-transitions",
        custom_role="VISIBILITY_REVIEWERS",
    )
    pod_id = ctx["pod_id"]
    role_name = ctx["custom_role"]

    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        f"visibility_workflow_{uuid4().hex[:8]}",
    )
    resources = [
        await _create_function(authenticated_client, pod_id),
        await _create_agent(authenticated_client, pod_id),
        workflow,
        await _create_app(authenticated_client, pod_id),
        await _create_table(authenticated_client, pod_id),
        await _create_file(authenticated_client, pod_id),
        await _create_schedule(
            authenticated_client,
            pod_id,
            workflow_name=workflow["payload"]["name"],
        ),
    ]

    for resource in resources:
        assert resource["payload"]["visibility"] == "PERSONAL"
        await _update_visibility(authenticated_client, pod_id, resource, "POD")
        await _update_visibility(authenticated_client, pod_id, resource, "PUBLIC")
        await _update_visibility(authenticated_client, pod_id, resource, "RESTRICTED")

        await _replace_role_grant(
            authenticated_client,
            pod_id,
            role_name,
            resource,
        )
        await _assert_role_has_grant(
            authenticated_client,
            pod_id,
            role_name,
            resource,
        )

        await _update_visibility(authenticated_client, pod_id, resource, "PERSONAL")
        await _assert_role_lacks_grant(
            authenticated_client,
            pod_id,
            role_name,
            resource,
        )


async def _create_function(client: AsyncClient, pod_id: str) -> dict:
    name = f"visibility_function_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": name,
            "description": "Visibility transition function",
            "visibility": "PERSONAL",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return {
        "resource_type": "function",
        "permission_ids": ["function.read"],
        "update": ("PATCH", f"/pods/{pod_id}/functions/{name}", "json"),
        "payload": response.json(),
    }


async def _create_agent(client: AsyncClient, pod_id: str) -> dict:
    name = f"visibility_agent_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": name,
            "instruction": "Help with visibility transition checks.",
            "visibility": "PERSONAL",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return {
        "resource_type": "agent",
        "permission_ids": ["agent.read"],
        "update": ("PATCH", f"/pods/{pod_id}/agents/{name}", "json"),
        "payload": response.json(),
    }


async def _create_workflow(client: AsyncClient, pod_id: str, name: str) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": name, "visibility": "PERSONAL"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return {
        "resource_type": "workflow",
        "permission_ids": ["workflow.read"],
        "update": ("PATCH", f"/pods/{pod_id}/workflows/{name}", "json"),
        "payload": response.json(),
    }


async def _create_app(client: AsyncClient, pod_id: str) -> dict:
    name = f"visibility_app_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/apps",
        json={"name": name, "visibility": "PERSONAL"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return {
        "resource_type": "app",
        "permission_ids": ["app.read"],
        "update": ("PATCH", f"/pods/{pod_id}/apps/{name}", "json"),
        "payload": response.json(),
    }


async def _create_table(client: AsyncClient, pod_id: str) -> dict:
    name = f"visibility_table_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "visibility": "PERSONAL",
            "columns": [{"name": "title", "type": "TEXT"}],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return {
        "resource_type": "datastore_table",
        "permission_ids": ["datastore.table.read"],
        "update": ("PATCH", f"/pods/{pod_id}/datastore/tables/{name}", "json"),
        "payload": response.json(),
    }


async def _create_file(client: AsyncClient, pod_id: str) -> dict:
    filename = f"visibility_file_{uuid4().hex[:8]}.md"
    response = await client.post(
        f"/pods/{pod_id}/datastore/files",
        data={
            "directory_path": "/",
            "visibility": "PERSONAL",
            "search_enabled": "false",
        },
        files={"data": (filename, b"# Visibility\n", "text/markdown")},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    return {
        "resource_type": "document",
        "permission_ids": ["folder.read"],
        "update": ("PATCH", f"/pods/{pod_id}/datastore/files/by-path", "form"),
        "payload": payload,
    }


async def _create_schedule(
    client: AsyncClient,
    pod_id: str,
    *,
    workflow_name: str,
) -> dict:
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": ScheduleType.TIME.value,
            "workflow_name": workflow_name,
            "config": {"scheduled_at": scheduled_at, "payload": {"source": "test"}},
            "visibility": "PERSONAL",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    return {
        "resource_type": "schedule",
        "permission_ids": ["schedule.read"],
        "update": ("PATCH", f"/pods/{pod_id}/schedules/{payload['id']}", "json"),
        "payload": payload,
    }


async def _update_visibility(
    client: AsyncClient,
    pod_id: str,
    resource: dict,
    visibility: str,
) -> None:
    method, path, encoding = resource["update"]
    if encoding == "form":
        response = await client.request(
            method,
            path,
            files={
                "path": (None, resource["payload"]["path"]),
                "visibility": (None, visibility),
            },
        )
    else:
        response = await client.request(method, path, json={"visibility": visibility})
    assert response.status_code == status.HTTP_200_OK, response.text
    resource["payload"] = response.json()
    assert resource["payload"]["pod_id"] == pod_id
    assert resource["payload"]["visibility"] == visibility


async def _replace_role_grant(
    client: AsyncClient,
    pod_id: str,
    role_name: str,
    resource: dict,
) -> None:
    response = await client.put(
        f"/pods/{pod_id}/roles/{role_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": resource["resource_type"],
                    "resource_name": _resource_name(resource),
                    "permission_ids": resource["permission_ids"],
                }
            ]
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text


async def _assert_role_has_grant(
    client: AsyncClient,
    pod_id: str,
    role_name: str,
    resource: dict,
) -> None:
    grants = await _role_grants(client, pod_id, role_name)
    assert _grant_key(resource) in grants


async def _assert_role_lacks_grant(
    client: AsyncClient,
    pod_id: str,
    role_name: str,
    resource: dict,
) -> None:
    grants = await _role_grants(client, pod_id, role_name)
    assert _grant_key(resource) not in grants


async def _role_grants(
    client: AsyncClient,
    pod_id: str,
    role_name: str,
) -> set[tuple[str, str]]:
    response = await client.get(f"/pods/{pod_id}/roles/{role_name}/permissions")
    assert response.status_code == status.HTTP_200_OK, response.text
    return {
        (grant["resource_type"], grant["resource_name"])
        for grant in response.json()["grants"]
    }


def _grant_key(resource: dict) -> tuple[str, str]:
    return (resource["resource_type"], _resource_name(resource))


def _resource_name(resource: dict) -> str:
    if resource["resource_type"] in {"folder", "document"}:
        return resource["payload"]["path"]
    return resource["payload"]["name"]
