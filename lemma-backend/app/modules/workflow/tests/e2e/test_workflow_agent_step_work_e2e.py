"""A workflow's AGENT step on the work runtime, against real Postgres, in-process.

The step starts its agent's conversation, which writes the agent's turn in the
same transaction; the turn's finalize emits ``AgentRunFinished``, whose
subscriber ``workflow.resume_for_agent`` moves the run on. No consumer group,
no sweep: if any of those writes were lost, the run would sit on its wait.

Also: an AGENT wait gets its ceiling as a ``workflow.wait_expire`` item, and at
that ceiling a conversation that already finished resumes the run (what the
reconciliation sweep did for a completion that reached nobody).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.infrastructure.work import WorkClass
from app.modules.agent.tests.e2e.turn_work_support import (
    drain,
    runtime_profile,
    scalar,
    turn_registry,
    write,
)
from app.modules.workflow.events.agent_resume_work import register_agent_resume_work
from app.modules.workflow.events.run_work import register_run_work

pytestmark = [pytest.mark.e2e]

CLASSES = [WorkClass.INTERACTIVE, WorkClass.AUTOMATION, WorkClass.EVENTS]


async def _agent_workflow(client, org_id: str) -> tuple[str, str]:
    """A pod, a mock-model agent, and a workflow of one AGENT step."""
    from uuid import uuid4

    pod = await client.post(
        "/pods",
        json={
            "name": f"Agent step {uuid4().hex[:6]}",
            "description": "workflow agent step e2e",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]
    profile = await runtime_profile(client, org_id)
    agent_name = f"stepper_{uuid4().hex[:6]}"
    agent = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Answer briefly.",
            "agent_runtime": {"profile_id": profile, "model_name": "mock-safe-model"},
        },
    )
    assert agent.status_code == 201, agent.text
    name = f"flow-{uuid4().hex[:8]}"
    start = {"type": "MANUAL"}
    created = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": name, "start": start, "mode": "GLOBAL"},
    )
    assert created.status_code == 201, created.text
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={
            "start": start,
            "nodes": [
                {
                    "id": "ask",
                    "type": "AGENT",
                    "config": {"agent_name": agent_name, "input_mapping": {}},
                },
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "ask", "target": "end"}],
        },
    )
    assert graph.status_code == 200, graph.text
    return pod_id, name


async def _start(client, pod_id: str, workflow: str) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflows/{workflow}/runs")
    assert response.status_code == 201, response.text
    return response.json()


async def _run(client, pod_id: str, run_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _registry():
    return turn_registry(extra=(register_run_work, register_agent_resume_work))


@pytest.mark.drives_work
async def test_an_agent_step_resumes_its_run_from_the_agents_finish(
    authenticated_client, fixed_test_org
) -> None:
    pod_id, workflow = await _agent_workflow(authenticated_client, fixed_test_org["id"])
    run = await _start(authenticated_client, pod_id, workflow)
    parked = await _run(authenticated_client, pod_id, run["id"])
    assert parked["status"] in {"RUNNING", "WAITING"}
    assert parked["active_wait"]["wait_type"] == "AGENT"
    conversation = parked["active_wait"]["external_ref"]
    # The agent's turn was written with the conversation the step started, in
    # the automation class; the step's ceiling with its wait.
    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'agent.turn.automation' "
            "AND concurrency_key = :k",
            k=f"conversation:{conversation}",
        )
        == 1
    )
    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'workflow.wait_expire'"
        )
        == 1
    )

    await drain(_registry(), classes=CLASSES)

    finished = await _run(authenticated_client, pod_id, run["id"])
    assert finished["status"] == "COMPLETED", finished
    assert "ask" in finished["execution_context"]
    assert (
        await scalar(
            "SELECT status FROM agent_conversations WHERE id = :c", c=UUID(conversation)
        )
        == "COMPLETED"
    )


@pytest.mark.drives_work
async def test_at_its_ceiling_a_finished_agent_step_resumes_rather_than_expires(
    authenticated_client, fixed_test_org
) -> None:
    """The completion reached nobody (the cutover's backlog, say): the expiry
    item finds the conversation finished and resumes the run with its output."""
    pod_id, workflow = await _agent_workflow(authenticated_client, fixed_test_org["id"])
    run = await _start(authenticated_client, pod_id, workflow)
    conversation = (await _run(authenticated_client, pod_id, run["id"]))["active_wait"][
        "external_ref"
    ]
    # The turn runs, but its resume never lands.
    await drain(turn_registry(), classes=[WorkClass.AUTOMATION])
    await write("DELETE FROM work_items WHERE kind = 'workflow.resume_for_agent'")
    stuck = await _run(authenticated_client, pod_id, run["id"])
    assert stuck["active_wait"]["wait_type"] == "AGENT"

    await write(
        "UPDATE work_items SET run_at = now() WHERE kind = 'workflow.wait_expire'"
    )
    await drain(_registry(), classes=[WorkClass.EVENTS])

    finished = await _run(authenticated_client, pod_id, run["id"])
    assert finished["status"] == "COMPLETED", finished
    assert conversation
