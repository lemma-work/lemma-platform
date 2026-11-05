"""Starting a workflow run twice with one `Idempotency-Key` starts it once.

The manual start takes no body, so a double click -- or a client retrying a
request whose answer it never got -- was a second run, each doing the work.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.e2e]


async def _workflow(client: AsyncClient, org_id: str) -> tuple[str, str]:
    pod = await client.post(
        "/pods",
        json={
            "name": f"Run keys {uuid4().hex[:6]}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]
    workflow = await client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": "intake",
            "start": {"type": "MANUAL"},
            "nodes": [
                {
                    "id": "ask",
                    "type": "FORM",
                    "label": "Ask",
                    "config": {
                        "input_schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        }
                    },
                },
                {"id": "end", "type": "END", "label": "Done"},
            ],
            "edges": [{"id": "e1", "source": "ask", "target": "end"}],
        },
    )
    assert workflow.status_code == 201, workflow.text
    return pod_id, "intake"


async def _start(client: AsyncClient, pod_id: str, name: str, key: str | None):
    response = await client.post(
        f"/pods/{pod_id}/workflows/{name}/runs",
        headers={"Idempotency-Key": key} if key is not None else None,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_the_same_key_returns_the_run_it_already_started(
    authenticated_client: AsyncClient, fixed_test_org
) -> None:
    client = authenticated_client
    pod_id, name = await _workflow(client, fixed_test_org["id"])

    first = await _start(client, pod_id, name, "click-1")
    again = await _start(client, pod_id, name, "click-1")
    other = await _start(client, pod_id, name, "click-2")
    unkeyed = await _start(client, pod_id, name, None)

    assert again == first
    assert len({first, other, unkeyed}) == 3
    listed = await client.get(f"/pods/{pod_id}/workflows/{name}/runs")
    assert listed.status_code == 200, listed.text
    assert sorted(run["id"] for run in listed.json()["items"]) == sorted(
        [first, other, unkeyed]
    )
