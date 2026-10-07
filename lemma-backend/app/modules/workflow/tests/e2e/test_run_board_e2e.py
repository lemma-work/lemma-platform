"""The in-progress board's reads: titled runs, their waits, one workflow's runs.

The board asks the space-wide run list for one workflow's runs still going,
and draws each with what it is about and who it is waiting on. This walks that
read end to end: `run_title` set on the workflow, a form answered so the title
fills in, and the list filtered to one workflow carrying `title` and
`waiting_on` for every run in it.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Run board {uuid4().hex[:6]}",
            "description": "Run board E2E pod",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _form(node_id: str, properties: dict) -> dict:
    return {
        "id": node_id,
        "type": "FORM",
        "label": node_id.title(),
        "config": {
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
            }
        },
    }


async def _workflow(
    client: AsyncClient, pod_id: str, name: str, run_title: list[str] | None = None
) -> dict:
    nodes = [
        _form("intake", {"name": {"type": "string"}, "role": {"type": "string"}}),
        _form("review", {"approved": {"type": "boolean"}}),
        {"id": "end", "type": "END", "label": "Done"},
    ]
    edges = [
        {"id": "e1", "source": "intake", "target": "review"},
        {"id": "e2", "source": "review", "target": "end"},
    ]
    body: dict = {
        "name": name,
        "start": {"type": "MANUAL"},
        "nodes": nodes,
        "edges": edges,
    }
    if run_title is not None:
        body["run_title"] = run_title
    response = await client.post(f"/pods/{pod_id}/workflows", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _run(client: AsyncClient, pod_id: str, workflow_name: str) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflows/{workflow_name}/runs")
    assert response.status_code == 201, response.text
    return response.json()


async def _in_flight(client: AsyncClient, pod_id: str, workflow_id: str) -> dict:
    response = await client.get(
        f"/pods/{pod_id}/workflow-runs",
        params=[
            ("status", "WAITING"),
            ("status", "RUNNING"),
            ("workflow_id", workflow_id),
        ],
    )
    assert response.status_code == 200, response.text
    return {run["id"]: run for run in response.json()["items"]}


@pytest.mark.asyncio
async def test_runs_in_flight_carry_their_title_and_their_wait(
    authenticated_client: AsyncClient, fixed_test_org
):
    client = authenticated_client
    pod_id = await _pod(client, fixed_test_org["id"])
    hiring = await _workflow(
        client, pod_id, "hiring", run_title=["intake.name", "intake.role"]
    )
    assert hiring["run_title"] == ["intake.name", "intake.role"]
    assert hiring["start"]["type"] == "MANUAL"
    other = await _workflow(client, pod_id, "other")

    answered = await _run(client, pod_id, "hiring")
    fresh = await _run(client, pod_id, "hiring")
    elsewhere = await _run(client, pod_id, "other")

    submitted = await client.post(
        f"/pods/{pod_id}/workflow-runs/{answered['id']}/form",
        json={
            "node_id": "intake",
            "inputs": {"name": "Priya Shah", "role": "Designer"},
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["title"] == "Priya Shah · Designer"

    runs = await _in_flight(client, pod_id, hiring["id"])
    assert set(runs) == {answered["id"], fresh["id"]}, "filtered to one workflow"
    assert elsewhere["id"] not in runs

    assert runs[answered["id"]]["title"] == "Priya Shah · Designer"
    assert runs[answered["id"]]["waiting_on"]["node_id"] == "review"
    assert runs[answered["id"]]["waiting_on"]["wait_type"] == "HUMAN"
    assert runs[answered["id"]]["waiting_on"]["since"]
    # Nothing it names is filled in yet: no title, rather than "None · None".
    assert runs[fresh["id"]]["title"] is None
    assert runs[fresh["id"]]["waiting_on"]["node_id"] == "intake"

    # The workflow's own run list says the same.
    listed = await client.get(f"/pods/{pod_id}/workflows/hiring/runs")
    assert listed.status_code == 200, listed.text
    by_id = {run["id"]: run for run in listed.json()["items"]}
    assert by_id[answered["id"]]["title"] == "Priya Shah · Designer"

    # A workflow without a title titles nothing, and still reports its wait.
    others = await _in_flight(client, pod_id, other["id"])
    assert others[elsewhere["id"]]["title"] is None
    assert others[elsewhere["id"]]["waiting_on"]["node_id"] == "intake"

    # The opened run carries both too.
    opened = await client.get(f"/pods/{pod_id}/workflow-runs/{answered['id']}")
    assert opened.json()["title"] == "Priya Shah · Designer"
    assert opened.json()["waiting_on"]["node_id"] == "review"


@pytest.mark.asyncio
async def test_run_title_is_edited_cleared_and_validated_without_touching_start(
    authenticated_client: AsyncClient, fixed_test_org
):
    client = authenticated_client
    pod_id = await _pod(client, fixed_test_org["id"])
    await _workflow(client, pod_id, "hiring")
    run = await _run(client, pod_id, "hiring")
    await client.post(
        f"/pods/{pod_id}/workflow-runs/{run['id']}/form",
        json={"node_id": "intake", "inputs": {"name": "Sam", "role": "Ops"}},
    )

    # Set after the fact: runs already in flight are titled on the next read.
    patched = await client.patch(
        f"/pods/{pod_id}/workflows/hiring", json={"run_title": ["intake.role"]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["run_title"] == ["intake.role"]
    assert patched.json()["start"]["type"] == "MANUAL"
    opened = await client.get(f"/pods/{pod_id}/workflow-runs/{run['id']}")
    assert opened.json()["title"] == "Ops"

    # Changing the trigger keeps the title, and the reverse.
    retriggered = await client.patch(
        f"/pods/{pod_id}/workflows/hiring", json={"start": {"type": "MANUAL"}}
    )
    assert retriggered.json()["run_title"] == ["intake.role"]

    refused = await client.patch(
        f"/pods/{pod_id}/workflows/hiring", json={"run_title": ["intake.[role"]}
    )
    assert refused.status_code == 422, refused.text

    cleared = await client.patch(
        f"/pods/{pod_id}/workflows/hiring", json={"run_title": []}
    )
    assert cleared.json()["run_title"] == []
    assert cleared.json()["start"]["type"] == "MANUAL"
    opened = await client.get(f"/pods/{pod_id}/workflow-runs/{run['id']}")
    assert opened.json()["title"] is None
