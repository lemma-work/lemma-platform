"""A DECISION node that asks a question, end to end.

The run suspends on a DECISION wait, the real worker's `ask_workflow_decision`
job asks the decisions module with no session open, and the run resumes down
the branch the answer picks. No Typesafe key is configured in e2e and the model
is the deterministic mock, so every answer here has one knowable source: the
inline definition's own rules, or no rung at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]

ACTION = {
    "type": "choice",
    "prompt": "What should happen to this request?",
    "options": {"act": "Handle it now.", "ignore": "Leave it."},
    "fallback": "ignore",
}


def _definition(**extra: object) -> dict[str, object]:
    return {
        "description": "Whether a request is handled now.",
        "questions": {"action": ACTION},
        **extra,
    }


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Decision question {uuid4().hex[:6]}",
            "description": "Workflow decision e2e pod",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _workflow(client: AsyncClient, pod_id: str, question: dict) -> str:
    created = await client.post(
        f"/pods/{pod_id}/workflows", json={"name": f"triage-{uuid4().hex[:6]}"}
    )
    assert created.status_code == 201, created.text
    name = created.json()["name"]
    # One END per place the question can send a run, and no edges: a branch
    # is reached through the question alone, as a rule's target is.
    ends = sorted({*question["branches"].values(), *[question.get("on_open")]} - {None})
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={
            "nodes": [
                {"id": "triage", "type": "DECISION", "config": {"question": question}},
                *({"id": end, "type": "END"} for end in ends),
            ],
            "edges": [],
        },
    )
    assert graph.status_code == 200, graph.text
    # The question comes back as it was saved.
    saved = next(node for node in graph.json()["nodes"] if node["id"] == "triage")
    assert saved["config"]["question"]["branches"] == question["branches"]
    return name


async def _start(client: AsyncClient, pod_id: str, workflow: str) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflows/{workflow}/runs")
    assert response.status_code == 201, response.text
    return response.json()


async def _settled(client: AsyncClient, pod_id: str, run_id: str) -> dict:
    async def probe() -> dict:
        response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
        assert response.status_code == 200, response.text
        return response.json()

    return await eventually(
        label="the decision job settles the run",
        probe=probe,
        done=lambda run: run["status"] in {"COMPLETED", "FAILED"},
        timeout_seconds=60,
        interval_seconds=0.2,
    )


def _ran(run: dict) -> list[str]:
    return [step["node_id"] for step in run["step_history"]]


@pytest.mark.asyncio
async def test_a_question_its_rules_answer_takes_that_branch(
    authenticated_client: AsyncClient, fixed_test_org, db_session, worker
):
    _ = worker
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    workflow = await _workflow(
        authenticated_client,
        pod_id,
        {
            "input": {"kind": {"type": "literal", "value": "refund"}},
            "definition": _definition(
                rules=[{"when": "kind == 'refund'", "answer": {"action": "act"}}]
            ),
            "branches": {"act": "acted", "ignore": "ignored"},
        },
    )

    started = await _start(authenticated_client, pod_id, workflow)
    # The engine never asks inside its transaction: the run is handed back
    # waiting on the question, which the worker then asks -- unless the worker
    # has already answered it by the time the response reads the wait.
    assert started["status"] == "RUNNING"
    if started["active_wait"] is not None:
        assert started["active_wait"]["wait_type"] == "DECISION"
        assert started["active_wait"]["external_ref"] == (
            f"workflow:{started['id']}:triage:0"
        )

    run = await _settled(authenticated_client, pod_id, started["id"])

    assert run["status"] == "COMPLETED", run.get("error")
    assert _ran(run) == ["triage", "acted"]
    triage = run["execution_context"]["triage"]
    assert triage["choice"] == "act"
    assert triage["answered_by"] == "rules"
    assert triage["open"] == []
    assert triage["matched_condition"] is None

    # Recorded once, under the step, where a retry or a redrive would find it.
    recorded = await db_session.execute(
        text("SELECT subject_key, decider_scope FROM decisions WHERE id = :id"),
        {"id": triage["decision_id"]},
    )
    assert recorded.one() == (f"workflow:{started['id']}:triage:0", "inline")


@pytest.mark.asyncio
async def test_a_question_no_rung_can_answer_goes_to_on_open(
    authenticated_client: AsyncClient, fixed_test_org, worker
):
    _ = worker
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    # Both options are reserved for rules, and no rule matches: whatever the
    # model rung says does not stand, so the question is left open.
    workflow = await _workflow(
        authenticated_client,
        pod_id,
        {
            "input": {"type": "literal", "value": "not sure"},
            "definition": _definition(
                policy={"rules_only": {"action": ["act", "ignore"]}}
            ),
            "branches": {"act": "acted", "ignore": "ignored"},
            "on_open": "review",
        },
    )

    started = await _start(authenticated_client, pod_id, workflow)
    run = await _settled(authenticated_client, pod_id, started["id"])

    assert run["status"] == "COMPLETED", run.get("error")
    assert _ran(run) == ["triage", "review"]
    triage = run["execution_context"]["triage"]
    assert triage["open"] == ["action"]
    # The fallback is what an open choice answers with, but no rung committed.
    assert triage["choice"] == "ignore"
    assert triage["answered_by"] is None


@pytest.mark.asyncio
async def test_asking_a_decider_the_pod_does_not_have_fails_the_run(
    authenticated_client: AsyncClient, fixed_test_org, worker
):
    _ = worker
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    workflow = await _workflow(
        authenticated_client,
        pod_id,
        {
            "input": {"type": "literal", "value": "anything"},
            "decider": "no-such-decider",
            "branches": {"act": "acted"},
        },
    )

    started = await _start(authenticated_client, pod_id, workflow)
    run = await _settled(authenticated_client, pod_id, started["id"])

    assert run["status"] == "FAILED"
    assert run["failed_node_id"] == "triage"
    assert "No decider named 'no-such-decider'" in run["error"]
    assert run["active_wait"] is None


@pytest.mark.asyncio
async def test_a_question_is_checked_when_the_graph_is_saved(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    created = await authenticated_client.post(
        f"/pods/{pod_id}/workflows", json={"name": f"invalid-{uuid4().hex[:6]}"}
    )
    assert created.status_code == 201, created.text
    name = created.json()["name"]

    async def save(config: dict) -> dict:
        response = await authenticated_client.put(
            f"/pods/{pod_id}/workflows/{name}/graph",
            json={
                "nodes": [
                    {"id": "triage", "type": "DECISION", "config": config},
                    {"id": "acted", "type": "END"},
                ],
                "edges": [{"id": "e1", "source": "triage", "target": "acted"}],
            },
        )
        assert response.status_code == 422, response.text
        return response.json()

    missing = await save(
        {
            "question": {
                "input": {"type": "literal", "value": 1},
                "definition": _definition(),
                "branches": {"act": "nowhere"},
            }
        }
    )
    assert missing["code"] == "WORKFLOW_GRAPH_INVALID"
    assert "branch 'act' targets missing node 'nowhere'" in missing["message"]

    neither = await save({"rules": []})
    assert neither["code"] == "WORKFLOW_GRAPH_INVALID"
    assert "has no rules and no question" in neither["message"]

    # A branch that names no option of an inline question is a typo, refused
    # before it can become an answer that silently falls through.
    typo = await save(
        {
            "question": {
                "input": {"type": "literal", "value": 1},
                "definition": _definition(),
                "branches": {"acts": "acted"},
            }
        }
    )
    assert "acts" in str(typo)
