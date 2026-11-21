"""FAILURE edges: where a node's failure goes instead of failing the run.

A graph without one behaves exactly as before; `test_stepper.py` and
`test_wait_failure.py` hold that. These cover the edge itself: what saving
accepts, what the run does when a node with one fails inline or while waiting,
and how a LOOP's edge catches its body.
"""

from uuid import UUID, uuid4

import pytest

from app.modules.workflow.domain.errors import GraphValidationError
from app.modules.workflow.domain.graph import (
    WorkflowEdge,
    WorkflowEdgeKind,
    WorkflowGraphValidator,
)
from app.modules.workflow.domain.nodes import (
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
)
from app.modules.workflow.domain.run import (
    StepStatus,
    WorkflowRunEntity,
    WorkflowRunStatus,
)
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitStatus,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution import wait_failure
from app.modules.workflow.execution.stepper import RunStepper, StepResult


# -- fixtures -----------------------------------------------------------------


class _Functions:
    """Inline results by function name; a name in `failing` raises instead."""

    def __init__(self, results=None, failing=()):
        self.results = results or {}
        self.failing = set(failing)
        self.calls: list[tuple[str, dict]] = []

    async def execute_function(self, function_name, inputs, pod_id, user_id, ctx=None):
        self.calls.append((function_name, inputs))
        if function_name in self.failing:
            raise RuntimeError(f"{function_name} exploded")
        return self.results.get(function_name, {"ok": True})


class _Agents:
    async def run_agent(self, *args, **kwargs):
        return uuid4()


class _Work:
    async def add(self, *args, **kwargs):
        return None


class _AnyAgent:
    async def require_execute(self, *, agent_name, pod_id, user_id):
        return None


def _stepper(functions: _Functions) -> RunStepper:
    return RunStepper(
        agent=_Agents(),  # type: ignore[arg-type]
        function=functions,  # type: ignore[arg-type]
        work=_Work(),  # type: ignore[arg-type]
        agent_access=_AnyAgent(),
    )


def _fn(node_id: str, name: str | None = None, **inputs: str) -> FunctionNode:
    mapping = {
        key: {"type": "expression", "value": value} for key, value in inputs.items()
    }
    return FunctionNode(
        id=node_id,
        config=FunctionNodeConfig(
            function_name=name or node_id,
            input_mapping=mapping,  # type: ignore[arg-type]
        ),
    )


def _edge(source: str, target: str) -> WorkflowEdge:
    return WorkflowEdge(id=f"{source}->{target}", source=source, target=target)


def _on_failure(source: str, target: str) -> WorkflowEdge:
    return WorkflowEdge(
        id=f"{source}-x->{target}",
        source=source,
        target=target,
        kind=WorkflowEdgeKind.FAILURE,
    )


def _flow(nodes, edges) -> WorkflowEntity:
    flow = WorkflowEntity(
        id=uuid4(), pod_id=uuid4(), name="failure-edges", nodes=nodes, edges=edges
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


def _issues(nodes, edges) -> list[str]:
    with pytest.raises(GraphValidationError) as exc_info:
        WorkflowGraphValidator.validate(nodes, edges)
    return exc_info.value.issues


# -- saving -------------------------------------------------------------------


def test_a_failure_edge_is_not_a_second_way_on():
    nodes = [_fn("save"), _fn("mark_failed"), EndNode(id="end")]
    edges = [
        _edge("save", "end"),
        _on_failure("save", "mark_failed"),
        _edge("mark_failed", "end"),
    ]
    # Its target has an incoming edge, so is not a second entry, and is
    # reachable only through the failure edge.
    assert WorkflowGraphValidator.validate(nodes, edges) == "save"


def test_an_edge_without_a_kind_is_a_next_edge():
    edge = WorkflowEdge.model_validate({"id": "e1", "source": "a", "target": "b"})
    assert edge.kind is WorkflowEdgeKind.NEXT


def test_one_failure_edge_per_node():
    nodes = [_fn("save"), _fn("a"), _fn("b"), EndNode(id="end")]
    edges = [
        _edge("save", "end"),
        _on_failure("save", "a"),
        _on_failure("save", "b"),
        _edge("a", "end"),
        _edge("b", "end"),
    ]
    assert "node 'save' has 2 failure edges; at most 1" in _issues(nodes, edges)


def test_an_end_has_no_failure_edge():
    nodes = [_fn("save"), EndNode(id="end"), _fn("after")]
    edges = [_edge("save", "end"), _on_failure("end", "after")]
    assert "end node 'end' must not have a failure edge" in _issues(nodes, edges)


def test_a_failure_edge_does_not_retry_its_own_node():
    nodes = [_fn("save"), EndNode(id="end")]
    edges = [_edge("save", "end"), _on_failure("save", "save")]
    assert "node 'save' failure edge cannot target itself" in _issues(nodes, edges)


def test_failure_edges_do_not_retry_each_other():
    # Two steps that each fail into the other would go round until the step
    # cap, running both again on every lap.
    nodes = [_fn("begin"), _fn("a"), _fn("b"), _fn("c"), EndNode(id="end")]
    edges = [
        _edge("begin", "a"),
        _edge("a", "end"),
        _edge("b", "end"),
        _edge("c", "end"),
        _on_failure("a", "b"),
        _on_failure("b", "c"),
        _on_failure("c", "a"),
    ]
    issues = _issues(nodes, edges)
    assert "failure edges loop back on themselves: 'a' -> 'b' -> 'c' -> 'a'" in issues
    assert len([i for i in issues if "loop back" in i]) == 1


def test_a_failure_chain_that_ends_is_accepted():
    nodes = [_fn("begin"), _fn("a"), _fn("b"), EndNode(id="end")]
    edges = [
        _edge("begin", "a"),
        _edge("a", "end"),
        _edge("b", "end"),
        _on_failure("a", "b"),
        _on_failure("begin", "b"),
    ]
    assert WorkflowGraphValidator.validate(nodes, edges) == "begin"


def test_a_loop_keeps_one_next_edge_beside_its_failure_edge():
    nodes = [
        _fn("load"),
        LoopNode(
            id="each",
            config=LoopNodeConfig(items_path="load.rows", child_node_id="row"),
        ),
        _fn("row"),
        _fn("mark_batch_failed"),
        EndNode(id="end"),
    ]
    edges = [
        _edge("load", "each"),
        _edge("each", "end"),
        _on_failure("each", "mark_batch_failed"),
        _edge("mark_batch_failed", "end"),
        _edge("row", "each"),
    ]
    assert WorkflowGraphValidator.validate(nodes, edges) == "load"
    two_next = [*edges, _edge("each", "mark_batch_failed")]
    assert any(
        "loop 'each' has 2 outgoing edges" in i for i in _issues(nodes, two_next)
    )


def test_a_failure_edge_listed_first_is_not_a_decisions_default():
    decision = DecisionNode(
        id="route",
        config=DecisionNodeConfig(
            rules=[DecisionRule(condition="`false`", next_node_id="yes")]
        ),
    )
    nodes = [decision, _fn("yes"), _fn("oops"), EndNode(id="end")]
    edges = [
        _on_failure("route", "oops"),
        _edge("route", "end"),
        _edge("yes", "end"),
        _edge("oops", "end"),
    ]
    flow = _flow(nodes, edges)
    assert flow.next_after("route") == "end"
    assert flow.failure_target("route") == "oops"


# -- inline failures ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_node_hands_its_error_to_its_failure_branch():
    flow = _flow(
        [_fn("save"), _fn("mark_failed", reason="save.error"), EndNode(id="end")],
        [
            _edge("save", "end"),
            _on_failure("save", "mark_failed"),
            _edge("mark_failed", "end"),
        ],
    )
    run = _run(flow)
    functions = _Functions(failing={"save"})

    await _stepper(functions).advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.error is None and run.failed_node_id is None
    reason = functions.calls[-1]
    assert reason[0] == "mark_failed"
    assert "save exploded" in reason[1]["reason"]
    assert [(s.node_id, s.status) for s in run.step_history] == [
        ("save", StepStatus.FAILED),
        ("mark_failed", StepStatus.COMPLETED),
        ("end", StepStatus.COMPLETED),
    ]
    assert "save exploded" in (run.step_history[0].error or "")


@pytest.mark.asyncio
async def test_a_failing_failure_branch_fails_the_run_there():
    flow = _flow(
        [_fn("save"), _fn("mark_failed"), EndNode(id="end")],
        [
            _edge("save", "end"),
            _on_failure("save", "mark_failed"),
            _edge("mark_failed", "end"),
        ],
    )
    run = _run(flow)

    await _stepper(_Functions(failing={"save", "mark_failed"})).advance(run, flow)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "mark_failed"
    assert "mark_failed exploded" in (run.error or "")


def _loop_flow(*, body_edge: str | None, loop_edge: str | None) -> WorkflowEntity:
    nodes = [
        _fn("seed"),
        LoopNode(
            id="each",
            config=LoopNodeConfig(items_path="seed.rows", child_node_id="row"),
        ),
        _fn("row", "row_fn", value="loop.item"),
        EndNode(id="end"),
    ]
    edges = [_edge("seed", "each"), _edge("row", "each"), _edge("each", "end")]
    if body_edge is not None:
        edges.append(_on_failure("row", body_edge))
    if loop_edge is not None:
        nodes.append(_fn(loop_edge))
        edges += [_on_failure("each", loop_edge), _edge(loop_edge, "end")]
    return _flow(nodes, edges)


class _FailsOnItem(_Functions):
    """`row_fn` fails on one item and echoes the others."""

    def __init__(self, bad):
        super().__init__()
        self.bad = bad

    async def execute_function(self, function_name, inputs, pod_id, user_id, ctx=None):
        self.calls.append((function_name, inputs))
        if function_name != "row_fn":
            return self.results.get(function_name, {"ok": True})
        if inputs.get("value") == self.bad:
            raise RuntimeError(f"row {self.bad} exploded")
        return {"value": inputs.get("value")}


def _seeded(rows: list[int], bad: int) -> _FailsOnItem:
    functions = _FailsOnItem(bad)
    functions.results["seed"] = {"rows": rows}
    return functions


@pytest.mark.asyncio
async def test_a_body_failure_routed_to_its_loop_skips_that_item():
    flow = _loop_flow(body_edge="each", loop_edge=None)
    run = _run(flow)

    await _stepper(_seeded([1, 2, 3], bad=2)).advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    results = run.execution_context.node_output("each")
    assert results is not None and results["count"] == 3
    assert [r.get("value") for r in results["results"]] == [1, None, 3]
    assert "row 2 exploded" in results["results"][1]["error"]


@pytest.mark.asyncio
async def test_a_loops_failure_edge_catches_a_body_failure_and_ends_the_loop():
    flow = _loop_flow(body_edge=None, loop_edge="handle")
    run = _run(flow)
    functions = _seeded([1, 2, 3], bad=2)

    await _stepper(functions).advance(run, flow)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_stack == []
    assert run.execution_context.loop is None
    caught = run.execution_context.node_output("each")
    assert caught is not None
    assert caught["failed_node_id"] == "row"
    assert "row 2 exploded" in caught["error"]
    assert caught["results"] == [{"value": 1}]
    # Item 3 was never started.
    assert [c[1].get("value") for c in functions.calls if c[0] == "row_fn"] == [1, 2]
    assert [c[0] for c in functions.calls][-1] == "handle"


@pytest.mark.asyncio
async def test_a_body_failure_with_no_edge_anywhere_still_fails_the_run():
    flow = _loop_flow(body_edge=None, loop_edge=None)
    run = _run(flow)

    await _stepper(_seeded([1, 2], bad=1)).advance(run, flow)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_node_id == "row"


# -- failures that arrive while a node waits -------------------------------------


class _Runs:
    def __init__(self, run):
        self.run = run

    async def get_for_update(self, run_id):
        return self.run

    async def update(self, run):
        return run


class _Waits:
    def __init__(self, active):
        self.active = active

    async def get_active_for_run(self, run_id):
        return self.active

    async def update(self, wait):
        return wait


class _Flows:
    def __init__(self, flow):
        self.flow = flow

    async def get(self, flow_id):
        return self.flow


class _Questions:
    def __init__(self):
        self.ended: list[UUID] = []

    async def run_ended(self, *, pod_id, run_id):
        self.ended.append(run_id)


class _Engine:
    """The engine surface `fail_run_for_wait` uses, over a real stepper."""

    def __init__(self, run, wait, flow, functions):
        self.run_repo = _Runs(run)
        self.wait_repo = _Waits(wait)
        self.flow_repo = _Flows(flow)
        self.form_questions = _Questions()
        self.functions = functions
        self.persisted: list[StepResult] = []
        self.settled: list[WorkflowRunEntity] = []

    def _stepper(self, ctx):
        return _stepper(self.functions)

    async def _persist_wait(self, run, result):
        self.persisted.append(result)

    async def _record_transition(self, run):
        return None

    async def _settle(self, run):
        self.settled.append(run)


@pytest.fixture
def _no_owner_lookup(monkeypatch):
    async def owner(engine, run):
        return None

    monkeypatch.setattr(wait_failure, "owner_context", owner)


def _waiting_on(run, node_id, wait_type) -> WorkflowRunWaitEntity:
    return WorkflowRunWaitEntity(
        run_id=run.id,
        flow_id=run.flow_id,
        pod_id=run.pod_id,
        node_id=node_id,
        wait_type=wait_type,
        external_ref=str(uuid4()),
    )


@pytest.mark.asyncio
async def test_a_function_run_that_fails_continues_down_the_failure_edge(
    _no_owner_lookup,
):
    flow = _flow(
        [_fn("save"), _fn("mark_failed", reason="save.error"), EndNode(id="end")],
        [
            _edge("save", "end"),
            _on_failure("save", "mark_failed"),
            _edge("mark_failed", "end"),
        ],
    )
    run = _run(flow)
    run.begin_step("save")
    wait = _waiting_on(run, "save", WorkflowRunWaitType.FUNCTION)
    functions = _Functions()
    engine = _Engine(run, wait, flow, functions)

    out = await wait_failure.fail_run_for_wait(
        engine,  # type: ignore[arg-type]
        wait,
        error="function runtime returned 500",
        output={"error": "function runtime returned 500", "partial": 1},
    )

    assert out is run
    assert wait.status == WorkflowRunWaitStatus.FAILED
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.execution_context.node_output("save") == {
        "error": "function runtime returned 500",
        "partial": 1,
    }
    assert functions.calls == [
        ("mark_failed", {"reason": "function runtime returned 500"})
    ]
    assert run.step_history[0].status == StepStatus.FAILED
    assert engine.settled == [run]
    assert engine.form_questions.ended == []


@pytest.mark.asyncio
async def test_an_expired_form_closes_its_question_before_its_branch_runs(
    _no_owner_lookup,
):
    flow = _flow(
        [
            FormNode(id="review", config=FormNodeConfig(input_schema={})),
            _fn("chase"),
            EndNode(id="end"),
        ],
        [_edge("review", "end"), _on_failure("review", "chase"), _edge("chase", "end")],
    )
    run = _run(flow)
    run.suspend_step(run.begin_step("review"), human_wait=True)
    assert run.status == WorkflowRunStatus.WAITING
    wait = _waiting_on(run, "review", WorkflowRunWaitType.HUMAN)
    engine = _Engine(run, wait, flow, _Functions())

    out = await wait_failure.fail_run_for_wait(
        engine,  # type: ignore[arg-type]
        wait,
        error="Nobody answered in time",
    )

    assert out is run
    assert run.status == WorkflowRunStatus.COMPLETED
    assert engine.form_questions.ended == [run.id]
    assert run.execution_context.node_output("review") == {
        "error": "Nobody answered in time"
    }
