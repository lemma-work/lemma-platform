"""A DECISION node: rules first, then its question, then the branch its answer picks.

The node never asks inside the stepper -- asking can take seconds, and the
stepper runs inside the engine's transaction. It suspends on a DECISION wait
carrying the question, and the answer comes back through a resume, which is
where its branch is taken. These drive both halves through the real stepper.
"""

from uuid import UUID, uuid4

import pytest

from app.modules.workflow.domain.decision_step import DecisionAsk, DecisionOutcome
from app.modules.workflow.domain.graph import WorkflowEdge
from app.modules.workflow.domain.nodes import (
    DecisionNode,
    DecisionNodeConfig,
    DecisionNodeQuestion,
    DecisionRule,
    EndNode,
    FunctionNode,
    FunctionNodeConfig,
    LoopNode,
    LoopNodeConfig,
)
from app.modules.workflow.domain.run import (
    StepStatus,
    WorkflowRunEntity,
    WorkflowRunStatus,
)
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution.stepper import RunStepper

pytestmark = pytest.mark.asyncio

TRIAGE = {
    "description": "What to do with a message.",
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should happen to it?",
            "options": {
                "act": "Needs a reply.",
                "ask": "Needs a person.",
                "ignore": "Needs nothing.",
            },
            "fallback": "ask",
        }
    },
}


class _Agents:
    async def run_agent(self, agent_name, input_data, pod_id, user_id, **kwargs):
        return uuid4()


class _Functions:
    """Every function returns its inputs, inline."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_function(self, function_name, inputs, pod_id, user_id, ctx=None):
        self.calls.append(function_name)
        return {"echo": inputs}


class _Timers:
    async def schedule_workflow_wake(self, run_id, scheduled_at, pod_id, user_id):
        return run_id


def _stepper(functions: _Functions | None = None) -> RunStepper:
    return RunStepper(
        agent=_Agents(),
        function=functions or _Functions(),
        schedule=_Timers(),
    )


def _function(node_id: str) -> FunctionNode:
    return FunctionNode(id=node_id, config=FunctionNodeConfig(function_name=node_id))


def _edge(source: str, target: str) -> WorkflowEdge:
    return WorkflowEdge(id=f"{source}->{target}", source=source, target=target)


def _triage(
    rules: list[DecisionRule] | None = None, **question: object
) -> DecisionNode:
    values: dict[str, object] = {
        "input": {
            "text": {"type": "expression", "value": "intake.echo"},
            "channel": {"type": "literal", "value": "email"},
        },
        "definition": TRIAGE,
        "branches": {"act": "reply", "ask": "review"},
    }
    return DecisionNode(
        id="triage",
        config=DecisionNodeConfig(
            rules=rules or [],
            question=DecisionNodeQuestion.model_validate({**values, **question}),
        ),
    )


def _flow(decision: DecisionNode, *also: str) -> WorkflowEntity:
    """intake -> triage -> {reply | review | (edge) fallthrough | *also} -> end."""
    ends = ["reply", "review", "fallthrough", *also]
    nodes = [
        _function("intake"),
        decision,
        *(_function(node_id) for node_id in ends),
        EndNode(id="end"),
    ]
    edges = [
        _edge("intake", "triage"),
        _edge("triage", "fallthrough"),
        *(_edge(node_id, "end") for node_id in ends),
    ]
    flow = WorkflowEntity(
        id=uuid4(), pod_id=uuid4(), name="triage", nodes=nodes, edges=edges
    )
    flow.validate_graph()
    return flow


def _run(flow: WorkflowEntity) -> WorkflowRunEntity:
    assert flow.entry_node_id is not None
    return WorkflowRunEntity.create(
        flow_id=flow.id,
        pod_id=flow.pod_id,
        user_id=uuid4(),
        entry_node_id=flow.entry_node_id,
    )


async def _answer(
    stepper: RunStepper,
    run: WorkflowRunEntity,
    flow: WorkflowEntity,
    outcome: DecisionOutcome,
) -> None:
    """What the decision job's resume does: record the outcome, then go on."""
    run.resume("triage", outcome.as_output())
    await stepper.continue_after(run, flow, "triage")


def _ran(run: WorkflowRunEntity) -> list[str]:
    return [step.node_id for step in run.step_history]


# -- rules, unchanged ----------------------------------------------------------


async def test_a_rules_only_decision_records_what_it_always_has():
    decision = DecisionNode(
        id="triage",
        config=DecisionNodeConfig(
            rules=[
                DecisionRule(condition="intake.missing", next_node_id="reply"),
                DecisionRule(condition="intake.gone", next_node_id="review"),
            ]
        ),
    )
    flow = _flow(decision)
    run = _run(flow)
    await _stepper().advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_context.nodes["triage"] == {"matched_condition": None}
    assert "fallthrough" in _ran(run)


# -- asking --------------------------------------------------------------------


async def test_a_matching_rule_wins_before_the_question_is_asked():
    flow = _flow(
        _triage(rules=[DecisionRule(condition="intake", next_node_id="reply")])
    )
    run = _run(flow)
    result = await _stepper().advance(run, flow)

    assert result.wait is None
    assert run.status == WorkflowRunStatus.COMPLETED
    assert _ran(run) == ["intake", "triage", "reply", "end"]
    assert run.execution_context.nodes["triage"] == {
        "matched_condition": "intake",
        "choice": None,
        "decision_id": None,
        "open": [],
        "answered_by": None,
    }


async def test_with_no_rule_matching_the_step_suspends_on_its_question():
    flow = _flow(_triage())
    run = _run(flow)
    result = await _stepper().advance(run, flow)

    # A machine wait: the run stays RUNNING, as it does on an agent or a timer.
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.current_node_id == "triage"
    assert result.wait is not None
    assert result.wait.wait_type == WorkflowRunWaitType.DECISION
    subject = f"workflow:{run.id}:triage:0"
    assert result.wait.external_ref == subject
    assert run.step_history[-1].external_ref == subject

    ask = DecisionAsk.model_validate(result.wait.payload)
    assert ask.subject == subject
    # Resolved when the step suspends, from the bindings.
    assert ask.state == {"text": {}, "channel": "email"}
    assert ask.definition is not None
    assert ask.decider is None


async def test_a_single_binding_is_the_state_itself():
    flow = _flow(_triage(input={"type": "literal", "value": "hello"}))
    run = _run(flow)
    result = await _stepper().advance(run, flow)

    assert result.wait is not None
    assert DecisionAsk.model_validate(result.wait.payload).state == "hello"


# -- the answer ----------------------------------------------------------------


async def test_an_answer_takes_its_branch():
    stepper = _stepper()
    flow = _flow(_triage())
    run = _run(flow)
    await stepper.advance(run, flow)

    decision_id = uuid4()
    await _answer(
        stepper,
        run,
        flow,
        DecisionOutcome(
            choice="act", decision_id=decision_id, answered_by="system_one"
        ),
    )

    assert run.status == WorkflowRunStatus.COMPLETED
    assert _ran(run) == ["intake", "triage", "reply", "end"]
    triage = run.execution_context.nodes["triage"]
    assert triage["choice"] == "act"
    assert UUID(triage["decision_id"]) == decision_id
    assert triage["answered_by"] == "system_one"
    assert run.step_history[1].status == StepStatus.COMPLETED


async def test_an_answer_with_no_branch_falls_through_to_the_edge():
    stepper = _stepper()
    flow = _flow(_triage())
    run = _run(flow)
    await stepper.advance(run, flow)

    await _answer(
        stepper, run, flow, DecisionOutcome(choice="ignore", decision_id=uuid4())
    )

    assert _ran(run) == ["intake", "triage", "fallthrough", "end"]


async def test_an_open_question_goes_to_on_open():
    stepper = _stepper()
    flow = _flow(_triage(on_open="escalate"), "escalate")
    run = _run(flow)
    await stepper.advance(run, flow)

    await _answer(
        stepper,
        run,
        flow,
        DecisionOutcome(choice="ask", decision_id=uuid4(), open=["action"]),
    )

    assert _ran(run) == ["intake", "triage", "escalate", "end"]


async def test_without_on_open_an_open_question_takes_its_fallbacks_branch():
    stepper = _stepper()
    flow = _flow(_triage())
    run = _run(flow)
    await stepper.advance(run, flow)

    await _answer(
        stepper,
        run,
        flow,
        DecisionOutcome(choice="ask", decision_id=uuid4(), open=["action"]),
    )

    assert _ran(run) == ["intake", "triage", "review", "end"]


# -- asked once per step -------------------------------------------------------


async def test_each_loop_iteration_asks_under_its_own_index():
    stepper = _stepper()
    loop = LoopNode(
        id="each",
        config=LoopNodeConfig(
            items_path="intake.echo.items || `[1, 2]`", child_node_id="triage"
        ),
    )
    decision = _triage(input={"type": "expression", "value": "loop.item"}, branches={})
    nodes = [_function("intake"), loop, decision, EndNode(id="end")]
    edges = [_edge("intake", "each"), _edge("triage", "each"), _edge("each", "end")]
    flow = WorkflowEntity(
        id=uuid4(), pod_id=uuid4(), name="each", nodes=nodes, edges=edges
    )
    flow.validate_graph()
    run = _run(flow)

    first = await stepper.advance(run, flow)
    assert first.wait is not None
    assert first.wait.external_ref == f"workflow:{run.id}:triage:0"
    assert DecisionAsk.model_validate(first.wait.payload).state == 1

    run.resume("triage", DecisionOutcome(choice="act").as_output())
    second = await stepper.continue_after(run, flow, "triage")
    assert second.wait is not None
    assert second.wait.external_ref == f"workflow:{run.id}:triage:1"

    run.resume("triage", DecisionOutcome(choice="ask").as_output())
    await stepper.continue_after(run, flow, "triage")
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_context.nodes["each"]["count"] == 2


async def test_a_cycle_back_to_the_question_asks_again_rather_than_repeating():
    # Reusing the first visit's decision would send every visit down the same
    # branch -- a loop through `again` with no way out.
    stepper = _stepper()
    decision = _triage(branches={"ask": "again", "act": "reply"})
    nodes = [
        _function("intake"),
        decision,
        _function("again"),
        _function("reply"),
        EndNode(id="end"),
    ]
    edges = [
        _edge("intake", "triage"),
        _edge("again", "triage"),
        _edge("reply", "end"),
    ]
    flow = WorkflowEntity(
        id=uuid4(), pod_id=uuid4(), name="cycle", nodes=nodes, edges=edges
    )
    flow.validate_graph()
    run = _run(flow)

    first = await stepper.advance(run, flow)
    assert first.wait is not None
    run.resume("triage", DecisionOutcome(choice="ask").as_output())
    second = await stepper.continue_after(run, flow, "triage")

    assert second.wait is not None
    assert second.wait.external_ref == f"{first.wait.external_ref}:2"
