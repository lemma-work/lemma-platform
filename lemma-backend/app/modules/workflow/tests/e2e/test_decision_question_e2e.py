"""A decision node that asks a question, through the real app and database.

The question is answered by the e2e stand-in model, which picks an allowed
value; what these tests prove is everything around it -- the run waits on a
DECISION wait, the answer's route is the branch taken, and a cancelled run is
not resumed by its answer. No sandbox: every node here is a decision or an end.

The ``workflow.decide`` item the suspension wrote is run in-process on the work
runtime (the real handler and the real decision maker) rather than left to a
worker, so the test does not depend on which session-scoped fixtures happen to
be running. A worker that does pick the item up first leaves the in-process
round nothing to do, and the run ends the same way.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workflow.events.run_work import register_run_work
from app.modules.workflow.events.step_jobs import register_step_jobs
from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel

pytestmark = [pytest.mark.e2e]

ROUTES = {"billing": "refund", "bug": "file_bug"}


def _triage(**question: object) -> list[dict]:
    return [
        {
            "id": "triage",
            "type": "DECISION",
            "config": {
                "question": {
                    "instruction": "Triage incoming support email for billing.",
                    "evidence": {
                        "type": "literal",
                        "value": {
                            "subject": "Charged twice",
                            "body": "We were billed twice. Please refund one.",
                        },
                    },
                    "answer": {
                        "type": "string",
                        "enum": ["billing", "bug", "other"],
                        "description": "What is this support email about?",
                    },
                    "routes": ROUTES,
                    "unsure_next_node_id": "ask_someone",
                    **question,
                }
            },
        },
        {"id": "refund", "type": "END"},
        {"id": "file_bug", "type": "END"},
        {"id": "ask_someone", "type": "END"},
        {"id": "anything_else", "type": "END"},
    ]


#: The default edge: where an answer with no route of its own goes.
DEFAULT_EDGE = [{"id": "e1", "source": "triage", "target": "anything_else"}]


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Decision workflow {uuid4().hex[:6]}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _workflow(client: AsyncClient, pod_id: str, nodes: list[dict]) -> dict:
    create = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": f"triage-{uuid4().hex[:6]}", "start": {"type": "MANUAL"}},
    )
    assert create.status_code == 201, create.text
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{create.json()['name']}/graph",
        json={"nodes": nodes, "edges": DEFAULT_EDGE},
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


_DECISIONS = WorkRegistry()
register_step_jobs(_DECISIONS)
register_run_work(_DECISIONS)


async def _start(client: AsyncClient, pod_id: str, name: str) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflows/{name}/runs")
    assert response.status_code == 201, response.text
    return response.json()


async def _get(client: AsyncClient, pod_id: str, run_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _answer() -> None:
    """Run the ``workflow.decide`` items the runs' suspensions wrote."""
    await run_until_idle(
        async_session_maker,
        registry=_DECISIONS,
        classes=[WorkClass.AUTOMATION, WorkClass.EVENTS],
    )


@pytest.mark.drives_work
@pytest.mark.asyncio
async def test_a_run_waits_on_its_question_then_takes_the_answers_branch(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    workflow = await _workflow(authenticated_client, pod_id, _triage())

    run = await _start(authenticated_client, pod_id, workflow["name"])

    assert run["status"] == "RUNNING", run
    wait = run["active_wait"]
    assert wait["wait_type"] == "DECISION", wait
    assert wait["node_id"] == "triage"
    assert wait["payload"]["evidence"]["subject"] == "Charged twice"

    await _answer()
    finished = await eventually(
        label="the run to finish once its question is answered",
        probe=lambda: _get(authenticated_client, pod_id, run["id"]),
        done=lambda state: state["status"] in {"COMPLETED", "FAILED"},
        timeout_seconds=40,
        interval_seconds=0.2,
    )

    assert finished["status"] == "COMPLETED", finished
    decided = finished["execution_context"]["triage"]
    assert decided["answer"] in {"billing", "bug", "other", None}, decided
    expected = (
        "ask_someone"
        if decided["answer"] is None
        else ROUTES.get(decided["answer"], "anything_else")
    )
    assert decided["route"] == expected, decided
    assert decided["provider"] == "model"
    taken = [step["node_id"] for step in finished["step_history"]]
    assert taken == ["triage", expected], taken


@pytest.mark.drives_work
@pytest.mark.asyncio
async def test_a_cancelled_run_is_not_resumed_by_its_answer(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user, db_session
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    workflow = await _workflow(authenticated_client, pod_id, _triage())
    run = await _start(authenticated_client, pod_id, workflow["name"])
    run_id, external_ref = run["id"], run["active_wait"]["external_ref"]

    cancel = await authenticated_client.post(
        f"/pods/{pod_id}/workflow-runs/{run_id}/cancel"
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"

    await _answer()

    after = await _get(authenticated_client, pod_id, run_id)
    assert after["status"] == "CANCELLED", after
    assert "triage" not in after["execution_context"], after
    status = await db_session.scalar(
        select(WorkflowRunWaitModel.status).where(
            WorkflowRunWaitModel.external_ref == external_ref
        )
    )
    assert status == "CANCELLED"


@pytest.mark.asyncio
async def test_a_question_whose_answers_have_nowhere_to_go_is_refused_at_save(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    create = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": f"triage-{uuid4().hex[:6]}", "start": {"type": "MANUAL"}},
    )
    assert create.status_code == 201, create.text

    graph = await authenticated_client.put(
        f"/pods/{pod_id}/workflows/{create.json()['name']}/graph",
        json={
            "nodes": _triage(unsure_next_node_id=None)[:3],
            "edges": [],
        },
    )

    assert graph.status_code == 422, graph.text
    message = graph.json()["message"]
    assert "no route for other" in message, message
    assert "no route for an unsure answer" in message, message
