"""A decision node that asks a question, through the real app and database.

The question is answered by the e2e stand-in model, which picks an allowed
value; what these tests prove is everything around it -- the run waits on a
DECISION wait, the answer's route is the branch taken, and a cancelled run is
not resumed by its answer. No sandbox: every node here is a decision or an end.

The job is driven in-process (`ask_waiting_decision` with the real waits and
the real decision maker) rather than left to a worker, so the test does not
depend on which session-scoped fixtures happen to be running. A worker that
does pick the job up first leaves the in-process call nothing to do, and the
run ends the same way.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.decisions.contracts.decide import decision_maker
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.events.decision_task import UnitOfWorkDecisionWaits
from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel
from app.modules.workflow.services.decision_step import ask_waiting_decision

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


class _HoldsTheJob:
    """A decision port that queues nothing, so no worker can answer first."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def ask_once_committed(self, external_ref: str) -> None:
        self.asked.append(external_ref)


async def _start_unqueued(workflow_id: str, user_id: str) -> tuple[str, str]:
    """Start a run whose decision job is held back: its ref, and the run's id."""
    held = _HoldsTheJob()
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        engine = build_workflow_engine(uow)
        engine.decision_adapter = held
        run = await engine.start_run(UUID(workflow_id), UUID(user_id))
    assert run.status.value == "RUNNING", run
    [external_ref] = held.asked
    return external_ref, str(run.id)


async def _start(client: AsyncClient, pod_id: str, name: str) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflows/{name}/runs")
    assert response.status_code == 201, response.text
    return response.json()


async def _get(client: AsyncClient, pod_id: str, run_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _answer(external_ref: str) -> None:
    await ask_waiting_decision(
        external_ref,
        attempt=1,
        waits=UnitOfWorkDecisionWaits(SessionUnitOfWorkFactory(async_session_maker)),
        maker=decision_maker(),
    )


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

    await _answer(wait["external_ref"])
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


@pytest.mark.asyncio
async def test_a_cancelled_run_is_not_resumed_by_its_answer(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user, db_session
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    workflow = await _workflow(authenticated_client, pod_id, _triage())
    external_ref, run_id = await _start_unqueued(workflow["id"], fixed_test_user["id"])

    cancel = await authenticated_client.post(
        f"/pods/{pod_id}/workflow-runs/{run_id}/cancel"
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"

    await _answer(external_ref)

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
