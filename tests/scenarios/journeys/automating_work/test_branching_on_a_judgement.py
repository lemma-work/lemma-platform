"""Automating work → a workflow that branches on a judgement, not on a fact.

A branch that depends on reading something -- is this email a refund request --
used to need an agent step whose only job was to pick a branch, and a rule that
parsed its answer. A decision step can ask the closed question itself and route
on the answer.

The stack's stand-in model answers with an allowed value, so what is proved
here is the product's half: the run waits for the answer, goes down the branch
that answer names, records both, and a question with an answer that leads
nowhere is refused when it is saved. What a real model concludes about a real
email is the provider's business.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Automating work"),
    capability("Run a workflow"),
]

#: The answer the run sees decides the branch; each one has its own end, so the
#: path the run took is readable off its step history.
BRANCHES = {"true": "refund", "false": "reply"}
UNSURE = "ask_a_person"


def _triage(*, routes: dict[str, str], unsure: str | None) -> list[dict]:
    question: dict[str, object] = {
        "instruction": "Triage incoming support email for the billing team.",
        "evidence": {
            "type": "literal",
            "value": {
                "from": "finance@customer.example.com",
                "subject": "Charged twice this month",
                "body": "We were billed twice for the same plan. Please refund one.",
            },
        },
        "answer": {
            "type": "boolean",
            "description": "Is this email asking for a refund?",
        },
        "routes": routes,
    }
    if unsure is not None:
        question["unsure_next_node_id"] = unsure
    return [
        {"id": "triage", "type": "DECISION", "config": {"question": question}},
        {"id": "refund", "type": "END"},
        {"id": "reply", "type": "END"},
        {"id": UNSURE, "type": "END"},
    ]


@pytest.fixture
async def a_pod(world):
    alice = await world.person("daniel")
    return alice, await alice.works_in("operations")


@scenario("A run asks its question, then takes the branch the answer names")
@proves("PS-FLOW-015")
@covers("workflow.graph.update", "workflow.run.create", "workflow.run.get")
async def test_a_run_takes_the_branch_its_answer_names(a_pod):
    alice, pod = a_pod
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=_triage(routes=BRANCHES, unsure=UNSURE),
        edges=[],
        in_pod=pod,
    )

    finished = await alice.runs_workflow(workflow["name"], in_pod=pod)

    assert str(finished.get("status")) == "COMPLETED", (
        f"a run that asked a closed question did not finish: {finished}"
    )
    decided = (finished.get("execution_context") or {}).get("triage") or {}
    answer = decided.get("answer")
    assert answer in (True, False, None), f"not an allowed answer: {decided}"
    expected = UNSURE if answer is None else BRANCHES["true" if answer else "false"]
    # The step says what was answered and where it went...
    assert decided.get("route") == expected, decided
    # ...and that is where the run actually went.
    taken = [step.get("node_id") for step in finished.get("step_history") or []]
    assert taken == ["triage", expected], (
        f"answered {answer!r} but the run went {taken}, not on to {expected!r}"
    )


@scenario("A question with an answer that leads nowhere is refused, naming it")
@proves("PS-FLOW-015")
@covers("workflow.graph.update")
async def test_an_unrouted_answer_is_refused_at_save(a_pod):
    alice, pod = a_pod
    workflow = await alice.creates_a_workflow(in_pod=pod)

    response = await alice.api.call(
        "PUT",
        f"/pods/{pod['id']}/workflows/{workflow['name']}/graph",
        json={
            # "Yes" goes somewhere; "no" and "can't tell" go nowhere at all.
            "nodes": _triage(routes={"true": "refund"}, unsure=None)[:2],
            "edges": [],
            "start": {"type": "MANUAL"},
        },
    )

    assert response.status_code == 422, response.text
    message = str(response.json().get("message"))
    assert "no route for false" in message, message
    assert "no route for an unsure answer" in message, message
