"""E2E: a run a person starts about one record.

An app button that prepares one meeting or looks into one deal names the row;
the platform reads it as the person and hands it to the run as a table change
would (`start.payload`, `start.metadata`). A row the person cannot read is no
row at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def _pod_with_a_deal(client: AsyncClient, org_id: str) -> tuple[str, str, str]:
    pod = await client.post(
        "/pods",
        json={"name": f"Deals {uuid4().hex[:8]}", "organization_id": org_id},
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]
    table = f"deals_{uuid4().hex[:6]}"
    made = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table,
            "enable_rls": False,
            "columns": [{"name": "title", "type": "TEXT"}],
        },
    )
    assert made.status_code == 201, made.text
    row = await client.post(
        f"/pods/{pod_id}/datastore/tables/{table}/records",
        json={"data": {"title": "Acme renewal"}},
    )
    assert row.status_code == 201, row.text
    return pod_id, table, str(row.json()["id"])


async def _waiting_workflow(client: AsyncClient, pod_id: str) -> str:
    """A workflow that waits on a form first, so its run stays to be read."""
    start = {"type": "MANUAL"}
    created = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": f"look-into-{uuid4().hex[:6]}", "start": start},
    )
    assert created.status_code == 201, created.text
    name = created.json()["name"]
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={
            "start": start,
            "nodes": [
                {
                    "id": "notes",
                    "type": "FORM",
                    "label": "Notes",
                    "config": {
                        "input_schema": {
                            "type": "object",
                            "properties": {"notes": {"type": "string"}},
                        }
                    },
                },
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "notes", "target": "end"}],
        },
    )
    assert graph.status_code == 200, graph.text
    return name


async def test_a_run_about_a_record_starts_with_that_record(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id, table, record_id = await _pod_with_a_deal(
        authenticated_client, fixed_test_org["id"]
    )
    workflow = await _waiting_workflow(authenticated_client, pod_id)

    run = await authenticated_client.post(
        f"/pods/{pod_id}/workflows/{workflow}/runs",
        json={"about": {"table": table, "record_id": record_id}},
    )

    assert run.status_code == 201, run.text
    start = run.json()["execution_context"]["start"]
    assert start["payload"]["title"] == "Acme renewal"
    assert start["metadata"] == {"table_name": table, "record_id": record_id}


async def test_a_record_nobody_can_read_is_no_record(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id, table, _ = await _pod_with_a_deal(
        authenticated_client, fixed_test_org["id"]
    )
    workflow = await _waiting_workflow(authenticated_client, pod_id)
    path = f"/pods/{pod_id}/workflows/{workflow}/runs"

    missing = await authenticated_client.post(
        path, json={"about": {"table": table, "record_id": str(uuid4())}}
    )
    no_table = await authenticated_client.post(
        path, json={"about": {"table": "nowhere", "record_id": str(uuid4())}}
    )
    free_form = await authenticated_client.post(path, json={"inputs": {"x": 1}})

    assert missing.status_code == 404, missing.text
    assert no_table.status_code == 404, no_table.text
    # A run still takes no input of its own.
    assert free_form.status_code == 422, free_form.text


async def test_a_run_with_no_body_is_as_it_was(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id, _, _ = await _pod_with_a_deal(authenticated_client, fixed_test_org["id"])
    workflow = await _waiting_workflow(authenticated_client, pod_id)

    run = await authenticated_client.post(f"/pods/{pod_id}/workflows/{workflow}/runs")

    assert run.status_code == 201, run.text
    assert "start" not in run.json()["execution_context"]
