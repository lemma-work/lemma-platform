"""A workflow form waiting on a member, on the organization's live socket --
real websocket, Postgres and Redis.

The run stops at a form assigned to one member: the ``org`` topic tells that
member, and nobody else, when it starts waiting on them and when it stops.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.test_support.e2e.live_socket import LiveClient, org_socket
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e]


async def _subscribed(live: LiveClient) -> None:
    answer = await live.subscribe("o", "org")
    assert answer["type"] == "subscribed", answer
    await live.event("o", "pods")


async def _form_for(scenario, member_id: str) -> str:
    """A workflow that stops at a form assigned to ``member_id``; its name."""
    client = scenario.owner_client
    made = await client.post(
        f"/pods/{scenario.pod_id}/workflows",
        json={"name": f"review-{uuid4().hex[:6]}", "start": {"type": "MANUAL"}},
    )
    assert made.status_code == 201, made.text
    name = made.json()["name"]
    graph = await client.put(
        f"/pods/{scenario.pod_id}/workflows/{name}/graph",
        json={
            "start": {"type": "MANUAL"},
            "nodes": [
                {
                    "id": "approve",
                    "type": "FORM",
                    "label": "Approve",
                    "config": {
                        "assignee_pod_member_id": member_id,
                        "input_schema": {
                            "type": "object",
                            "properties": {"approved": {"type": "boolean"}},
                            "required": ["approved"],
                        },
                    },
                },
                {"id": "end", "type": "END", "label": "Done"},
            ],
            "edges": [{"id": "e1", "source": "approve", "target": "end"}],
        },
    )
    assert graph.status_code == 200, graph.text
    return name


async def test_a_form_waiting_on_a_member_reaches_that_member_alone(
    scenario, backend_server
) -> None:
    await scenario.create_org_with_pod(name_prefix="Org live waits")
    reviewer = await scenario.create_user("org-live-reviewer")
    await scenario.add_user_to_pod(user=reviewer, role="POD_EDITOR")
    found = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/members/lookup/by-user-id/{reviewer['id']}"
    )
    assert found.status_code == 200, found.text
    workflow = await _form_for(scenario, str(found.json()["pod_member_id"]))

    async with (
        org_socket(backend_server, scenario.org_id, reviewer["token"]) as assignee,
        org_socket(
            backend_server, scenario.org_id, scenario.owner_user["token"]
        ) as owner,
    ):
        await _subscribed(assignee)
        await _subscribed(owner)

        started = await scenario.owner_client.post(
            f"/pods/{scenario.pod_id}/workflows/{workflow}/runs"
        )
        assert started.status_code == 201, started.text
        run = started.json()
        assert run["status"] == "WAITING", run

        waiting = await assignee.event(
            "o", "waiting.started", workflow_run_id=run["id"]
        )
        assert waiting["kind"] == "workflow"
        assert waiting["pod_id"] == scenario.pod_id
        assert waiting["workflow_name"] == workflow

        answered = await scenario.async_client.post(
            f"/pods/{scenario.pod_id}/workflow-runs/{run['id']}/form",
            json={"node_id": "approve", "inputs": {"approved": True}},
            headers=auth_headers(reviewer),
        )
        assert answered.status_code == 200, answered.text
        await assignee.event("o", "waiting.stopped", workflow_run_id=run["id"])
        await owner.quiet()

    assert not owner.events("o", "waiting.started")
