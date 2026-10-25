"""Whose authority a workflow run's steps use, end to end through the API.

* An agent step asks ``agent.execute`` of the run's person, as a function step
  asks ``function.execute`` (M18): starting a workflow is not a grant to run
  every agent it names.
* A run a person retries continues as them, not as whoever started it.

Driven through the API against Postgres; no model, worker or sandbox is needed
-- an agent step's conversation is only started, never answered.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.core.infrastructure.db.session import async_session_maker
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)
from app.modules.workflow.infrastructure.models import WorkflowRunModel
from app.modules.workflow.tests.e2e.test_flow_execution_e2e import (
    _create_pod,
    _create_run,
    _create_workflow,
    _get_run,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _editor(
    admin: AsyncClient, async_client: AsyncClient, *, org_id: str, pod_id: str
) -> dict:
    user = await signup_user(async_client, "workflow-authority-editor")
    org_member = await invite_org_member(admin, async_client, org_id=org_id, user=user)
    await add_pod_member(
        admin, pod_id=pod_id, organization_member_id=org_member["id"], role="POD_EDITOR"
    )
    return user


async def _restricted_agent(client: AsyncClient, pod_id: str) -> str:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"restricted_{uuid4().hex[:6]}",
            "instruction": "Only the admin may run this.",
            "visibility": "RESTRICTED",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


async def test_an_agent_step_the_runs_person_may_not_execute_fails_unstarted(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    admin = authenticated_client
    pod_id = await _create_pod(admin, fixed_test_org["id"], "Agent Step Access")
    agent_name = await _restricted_agent(admin, pod_id)
    workflow = await _create_workflow(
        admin,
        pod_id,
        name=f"agent-access-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "ask",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "ask", "target": "end"}],
    )
    editor = await _editor(
        admin, async_client, org_id=fixed_test_org["id"], pod_id=pod_id
    )
    # The agent is RESTRICTED to the admin: the editor cannot even see it...
    hidden = await async_client.get(
        f"/pods/{pod_id}/agents/{agent_name}", headers=auth_headers(editor)
    )
    assert hidden.status_code in {403, 404}, hidden.text

    # ...so a workflow they may start does not run it for them.
    run = await _create_run(
        async_client, pod_id, workflow["name"], headers=auth_headers(editor)
    )

    assert run["status"] == "FAILED"
    assert run["failed_node_id"] == "ask"
    assert "agent.execute" in run["error"]
    assert run["active_wait"] is None

    # The admin, who may run the agent, gets the step started.
    allowed = await _create_run(admin, pod_id, workflow["name"])
    assert allowed["status"] == "RUNNING"
    assert allowed["active_wait"]["wait_type"] == "AGENT"


async def test_a_run_retried_by_another_member_continues_as_them(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
):
    admin = authenticated_client
    pod_id = await _create_pod(admin, fixed_test_org["id"], "Retry Authority")
    workflow = await _create_workflow(
        admin,
        pod_id,
        name=f"retry-authority-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "route",
                "type": "DECISION",
                "config": {
                    "rules": [{"condition": "`true` == `true`", "next_node_id": "end"}]
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "route", "target": "end"}],
    )
    run = await _create_run(admin, pod_id, workflow["name"])
    assert run["user_id"] == fixed_test_user["id"]
    # Failed at the decision, as a runtime error in its condition would leave
    # it: a routing node is one a retry recomputes.
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == UUID(run["id"]))
            .values(
                status="FAILED",
                current_node_id="route",
                failed_node_id="route",
                error="condition could not be evaluated",
                completed_at=datetime.now(timezone.utc),
            )
        )
    editor = await _editor(
        admin, async_client, org_id=fixed_test_org["id"], pod_id=pod_id
    )

    retried = await async_client.post(
        f"/pods/{pod_id}/workflow-runs/{run['id']}/retry",
        headers=auth_headers(editor),
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["user_id"] == editor["id"]
    assert (await _get_run(admin, pod_id, run["id"]))["status"] == "COMPLETED"
