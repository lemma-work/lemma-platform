"""Stepper behavior: linear advance, branching, loops, suspends, failures."""

from uuid import uuid4

import pytest

from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.domain.graph import WorkflowEdge
from app.modules.workflow.domain.nodes import (
    AgentNode,
    AgentNodeConfig,
    DecisionNode,
    DecisionNodeConfig,
    DecisionRule,
    EndNode,
    FormNode,
    FormNodeConfig,
    FunctionNode,
    FunctionNodeConfig,
    LoopNode,
    LoopNodeConfig,
    WaitUntilNode,
    WaitUntilNodeConfig,
)
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus, StepStatus
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.stepper import RunStepper

pytestmark = pytest.mark.asyncio


class StubAgentPort:
    def __init__(self):
        self.calls: list[dict] = []

    async def run_agent(self, agent_name, input_data, pod_id, user_id, **kwargs):
        self.calls.append({"agent_name": agent_name, "input_data": input_data})
        return uuid4()

    async def get_conversation_status(self, conversation_id):
        return {"status": "RUNNING"}


class StubFunctionPort:
    def __init__(self, results=None):
        self.calls: list[dict] = []
        self._results = results or {}

    async def execute_function(self, function_name, inputs, pod_id, user_id, ctx=None):
        self.calls.append({"function_name": function_name, "inputs": inputs})
        result = self._results.get(function_name, {"ok": True})
        if callable(result):
            return result(inputs)
        return result

    async def get_run_status(self, function_run_id):
        return {"status": "RUNNING"}


class StubSchedulePort:
    async def schedule_workflow_wake(self, run_id, scheduled_at, pod_id, user_id):
        return run_id


def _flow(nodes, edges) -> WorkflowEntity:
    flow = WorkflowEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="test-flow",
        nodes=nodes,
        edges=edges,
    )
    flow.validate_graph()
    return flow


def _run(flow: WorkflowEntity) -> WorkflowRunEntity:
    return WorkflowRunEntity.create(
        flow_id=flow.id,
        pod_id=flow.pod_id,
        user_id=uuid4(),
        entry_node_id=flow.entry_node_id,
    )


def _stepper(function_results=None, agent=None, function=None) -> RunStepper:
    return RunStepper(
        agent=agent or StubAgentPort(),
        function=function or StubFunctionPort(function_results),
        schedule=StubSchedulePort(),
    )


def _edge(i, source, target):
    return WorkflowEdge(id=f"e{i}", source=source, target=target)


async def test_linear_flow_completes_and_records_outputs():
    nodes = [
        FunctionNode(id="a", config=FunctionNodeConfig(function_name="fn_a")),
        FunctionNode(id="b", config=FunctionNodeConfig(function_name="fn_b")),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "a", "b"), _edge(2, "b", "end")])
    run = _run(flow)
    result = await _stepper({"fn_a": {"x": 1}, "fn_b": {"y": 2}}).advance(run, flow)

    assert result.wait is None
    assert run.status == WorkflowRunStatus.COMPLETED
    view = run.execution_context.to_view()
    assert view["a"] == {"x": 1}
    assert view["b"] == {"y": 2}
    assert [s.node_id for s in run.step_history] == ["a", "b", "end"]
    assert all(s.status == StepStatus.COMPLETED for s in run.step_history)


async def test_form_entry_suspends_immediately_with_human_wait():
    nodes = [
        FormNode(id="intake", config=FormNodeConfig(input_schema={"type": "object"})),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "intake", "end")])
    run = _run(flow)
    result = await _stepper().advance(run, flow)

    assert run.status == WorkflowRunStatus.WAITING
    assert run.current_node_id == "intake"
    assert result.wait is not None
    assert result.wait.wait_type == WorkflowRunWaitType.HUMAN
    assert result.wait.payload["input_schema"] == {"type": "object"}


async def test_form_resolves_schema_expr_against_context():
    nodes = [
        FunctionNode(id="src", config=FunctionNodeConfig(function_name="fn")),
        FormNode(
            id="intake",
            config=FormNodeConfig(
                input_schema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": {"type": "expression", "value": "src.labels"},
                        },
                        "body": {
                            "type": "string",
                            "default": {"type": "expression", "value": "src.draft"},
                        },
                    },
                }
            ),
        ),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "src", "intake"), _edge(2, "intake", "end")])
    run = _run(flow)
    result = await _stepper(
        {"fn": {"labels": ["a", "b"], "draft": "hello"}}
    ).advance(run, flow)

    assert run.status == WorkflowRunStatus.WAITING
    schema = result.wait.payload["input_schema"]
    assert schema["properties"]["category"]["enum"] == ["a", "b"]
    assert schema["properties"]["body"]["default"] == "hello"


async def test_form_fails_when_required_binding_resolves_to_nothing():
    nodes = [
        FunctionNode(id="src", config=FunctionNodeConfig(function_name="fn")),
        FormNode(
            id="intake",
            config=FormNodeConfig(
                input_schema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": {"type": "expression", "value": "src.missing"},
                        }
                    },
                }
            ),
        ),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "src", "intake"), _edge(2, "intake", "end")])
    run = _run(flow)
    result = await _stepper({"fn": {"labels": ["a"]}}).advance(run, flow)

    assert result.wait is None
    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "intake"
    # A required expression binding that resolves to nothing fails the node,
    # naming the expression — same as input_mapping.
    assert "src.missing" in (run.error or "")


async def test_resume_after_form_continues_to_completion():
    nodes = [
        FormNode(id="intake", config=FormNodeConfig(input_schema={"type": "object"})),
        FunctionNode(
            id="save",
            config=FunctionNodeConfig(
                function_name="fn",
                input_mapping={
                    "amount": {"type": "expression", "value": "intake.amount"}
                },
            ),
        ),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "intake", "save"), _edge(2, "save", "end")])
    run = _run(flow)
    stepper = _stepper()
    function = StubFunctionPort()
    stepper._function = function
    await stepper.advance(run, flow)

    run.resume("intake", {"amount": 42})
    result = await stepper.continue_after(run, flow, "intake")

    assert result.wait is None
    assert run.status == WorkflowRunStatus.COMPLETED
    assert function.calls == [{"function_name": "fn", "inputs": {"amount": 42}}]
    assert run.execution_context.to_view()["intake"] == {"amount": 42}


async def test_decision_routes_by_first_truthy_rule():
    nodes = [
        FormNode(id="intake", config=FormNodeConfig(input_schema={})),
        DecisionNode(
            id="route",
            config=DecisionNodeConfig(
                rules=[
                    DecisionRule(
                        condition="intake.approved == `true`",
                        next_node_id="approved_end",
                    ),
                    DecisionRule(condition="`true`", next_node_id="rejected_end"),
                ]
            ),
        ),
        EndNode(id="approved_end"),
        EndNode(id="rejected_end"),
    ]
    flow = _flow(nodes, [_edge(1, "intake", "route")])
    run = _run(flow)
    stepper = _stepper()
    await stepper.advance(run, flow)
    run.resume("intake", {"approved": True})
    await stepper.continue_after(run, flow, "intake")

    assert run.status == WorkflowRunStatus.COMPLETED
    assert [s.node_id for s in run.step_history] == ["intake", "route", "approved_end"]


async def test_decision_without_match_falls_through_default_edge():
    nodes = [
        FunctionNode(id="a", config=FunctionNodeConfig(function_name="fn")),
        DecisionNode(
            id="route",
            config=DecisionNodeConfig(
                rules=[
                    DecisionRule(condition="a.never == `true`", next_node_id="special")
                ]
            ),
        ),
        FunctionNode(id="special", config=FunctionNodeConfig(function_name="special")),
        EndNode(id="end"),
    ]
    flow = _flow(
        nodes,
        [
            _edge(1, "a", "route"),
            _edge(2, "route", "end"),
            _edge(3, "special", "end"),
        ],
    )
    run = _run(flow)
    await _stepper().advance(run, flow)
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_context.to_view()["route"] == {"matched_condition": None}


async def test_loop_iterates_body_and_aggregates_results():
    nodes = [
        FunctionNode(id="fetch", config=FunctionNodeConfig(function_name="fetch")),
        LoopNode(
            id="each",
            config=LoopNodeConfig(
                items_path="fetch.items", item_var_name="line", child_node_id="record"
            ),
        ),
        FunctionNode(
            id="record",
            config=FunctionNodeConfig(
                function_name="record",
                input_mapping={
                    "merchant": {"type": "expression", "value": "loop.line.merchant"},
                    "index": {"type": "expression", "value": "loop.index"},
                },
            ),
        ),
        EndNode(id="end"),
    ]
    flow = _flow(
        nodes,
        [
            _edge(1, "fetch", "each"),
            _edge(2, "each", "end"),
            _edge(3, "record", "each"),
        ],
    )
    run = _run(flow)
    items = [{"merchant": "Uber"}, {"merchant": "Payroll"}]
    function = StubFunctionPort(
        {
            "fetch": {"items": items},
            "record": lambda inputs: {"recorded": inputs["merchant"]},
        }
    )
    await _stepper(function=function).advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    view = run.execution_context.to_view()
    assert view["each"] == {
        "results": [{"recorded": "Uber"}, {"recorded": "Payroll"}],
        "count": 2,
    }
    record_calls = [c for c in function.calls if c["function_name"] == "record"]
    assert [c["inputs"]["index"] for c in record_calls] == [0, 1]
    # Loop state lives only in the stack (now empty) — never in node outputs.
    assert run.execution_stack == []
    assert "loop" not in view


async def test_loop_with_zero_items_advances_past_loop():
    nodes = [
        FunctionNode(id="fetch", config=FunctionNodeConfig(function_name="fetch")),
        LoopNode(
            id="each",
            config=LoopNodeConfig(items_path="fetch.items", child_node_id="record"),
        ),
        FunctionNode(id="record", config=FunctionNodeConfig(function_name="record")),
        EndNode(id="end"),
    ]
    flow = _flow(
        nodes,
        [_edge(1, "fetch", "each"), _edge(2, "each", "end")],
    )
    run = _run(flow)
    function = StubFunctionPort({"fetch": {"items": []}})
    await _stepper(function=function).advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_context.to_view()["each"] == {"results": [], "count": 0}
    assert all(c["function_name"] != "record" for c in function.calls)


async def test_suspend_inside_loop_body_resumes_iteration():
    nodes = [
        FunctionNode(id="fetch", config=FunctionNodeConfig(function_name="fetch")),
        LoopNode(
            id="each",
            config=LoopNodeConfig(items_path="fetch.items", child_node_id="ask"),
        ),
        FormNode(id="ask", config=FormNodeConfig(input_schema={})),
        EndNode(id="end"),
    ]
    flow = _flow(
        nodes,
        [_edge(1, "fetch", "each"), _edge(2, "each", "end")],
    )
    run = _run(flow)
    stepper = _stepper(function=StubFunctionPort({"fetch": {"items": [1, 2]}}))

    result = await stepper.advance(run, flow)
    assert run.status == WorkflowRunStatus.WAITING
    assert run.current_node_id == "ask"
    assert result.wait is not None

    run.resume("ask", {"answer": "first"})
    await stepper.continue_after(run, flow, "ask")
    assert run.status == WorkflowRunStatus.WAITING  # second iteration waits again

    run.resume("ask", {"answer": "second"})
    await stepper.continue_after(run, flow, "ask")
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_context.to_view()["each"] == {
        "results": [{"answer": "first"}, {"answer": "second"}],
        "count": 2,
    }


async def test_nested_loops():
    nodes = [
        FunctionNode(id="fetch", config=FunctionNodeConfig(function_name="fetch")),
        LoopNode(
            id="outer",
            config=LoopNodeConfig(
                items_path="fetch.groups", item_var_name="group", child_node_id="inner"
            ),
        ),
        LoopNode(
            id="inner",
            config=LoopNodeConfig(items_path="loop.group", child_node_id="record"),
        ),
        FunctionNode(
            id="record",
            config=FunctionNodeConfig(
                function_name="record",
                input_mapping={"value": {"type": "expression", "value": "loop.item"}},
            ),
        ),
        EndNode(id="end"),
    ]
    flow = _flow(
        nodes,
        [_edge(1, "fetch", "outer"), _edge(2, "outer", "end")],
    )
    run = _run(flow)
    function = StubFunctionPort(
        {
            "fetch": {"groups": [[1, 2], [3]]},
            "record": lambda inputs: {"value": inputs["value"]},
        }
    )
    await _stepper(function=function).advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    recorded = [
        c["inputs"]["value"] for c in function.calls if c["function_name"] == "record"
    ]
    assert recorded == [1, 2, 3]
    assert run.execution_context.to_view()["outer"]["count"] == 2


async def test_agent_node_suspends_with_external_ref():
    nodes = [
        AgentNode(id="parse", config=AgentNodeConfig(agent_name="parser")),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "parse", "end")])
    run = _run(flow)
    result = await _stepper().advance(run, flow)

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.step_history[-1].status == StepStatus.RUNNING
    assert result.wait.wait_type == WorkflowRunWaitType.AGENT
    assert result.wait.external_ref


async def test_job_function_suspends_with_function_wait_but_run_stays_running():
    nodes = [
        FunctionNode(id="export", config=FunctionNodeConfig(function_name="job_fn")),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "export", "end")])
    run = _run(flow)
    result = await _stepper(
        {
            "job_fn": {
                "run_id": str(uuid4()),
                "status": "PENDING",
                "function_type": "JOB",
            }
        }
    ).advance(run, flow)

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.current_node_id == "export"
    assert run.step_history[-1].status == StepStatus.RUNNING
    assert result.wait is not None
    assert result.wait.wait_type == WorkflowRunWaitType.FUNCTION
    assert result.wait.external_ref


async def test_function_node_suspends_on_dispatched_run():
    # Function nodes are dispatch-and-suspend for every type: the adapter enqueues
    # the run and returns it PENDING, so the stepper suspends on a FUNCTION wait
    # (the engine commits + releases its run-row lock) rather than blocking inline.
    run_id = uuid4()
    nodes = [
        FunctionNode(id="api", config=FunctionNodeConfig(function_name="api_fn")),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "api", "end")])
    run = _run(flow)
    result = await _stepper(
        {"api_fn": {"run_id": str(run_id), "status": "PENDING"}}
    ).advance(run, flow)

    assert run.status == WorkflowRunStatus.RUNNING
    assert result.wait is not None
    assert result.wait.wait_type == WorkflowRunWaitType.FUNCTION
    assert result.wait.external_ref == str(run_id)


async def test_wait_until_suspends_with_time_wait():
    nodes = [
        WaitUntilNode(id="cooldown", config=WaitUntilNodeConfig(timeout_seconds=60)),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "cooldown", "end")])
    run = _run(flow)
    result = await _stepper().advance(run, flow)

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.step_history[-1].status == StepStatus.RUNNING
    assert result.wait.wait_type == WorkflowRunWaitType.TIME
    assert result.wait.external_ref == str(run.id)
    assert result.wait.scheduled_at is not None


async def test_missing_required_input_fails_run_with_path_in_error():
    nodes = [
        FunctionNode(
            id="save",
            config=FunctionNodeConfig(
                function_name="fn",
                input_mapping={
                    "amount": {"type": "expression", "value": "intake.amount"}
                },
            ),
        ),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "save", "end")])
    run = _run(flow)
    await _stepper().advance(run, flow)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "save"
    assert "intake.amount" in run.error
    assert "amount" in run.error


async def test_executor_exception_fails_run():
    class ExplodingFunctionPort(StubFunctionPort):
        async def execute_function(self, *args, **kwargs):
            raise RuntimeError("boom")

    nodes = [
        FunctionNode(id="save", config=FunctionNodeConfig(function_name="fn")),
        EndNode(id="end"),
    ]
    flow = _flow(nodes, [_edge(1, "save", "end")])
    run = _run(flow)
    await _stepper(function=ExplodingFunctionPort()).advance(run, flow)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "save"
    assert "boom" in run.error
    assert run.step_history[-1].status == StepStatus.FAILED
