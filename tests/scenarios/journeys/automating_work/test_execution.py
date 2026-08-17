"""Automating work → running a function, and running a workflow.

The critical path of the product: code a person wrote, executing in a sandbox,
with a result they can see. Every scenario here provisions a real sandbox, so
they sit in the `sandbox` lane (`make scenarios-sandbox`).
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.building import function_source

pytestmark = [
    journey("Automating work"),
    capability("Run a function and see what happened"),
    pytest.mark.sandbox,
]


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person runs a function and gets its output")
@proves("PS-FUNC-001", "PS-FUNC-010")
@covers("function.run", "function.run.get", "function.created")
async def test_a_function_runs_and_returns_its_output(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)

    run = await alice.runs_function(
        function["name"], with_input={"value": 41}, in_pod=the_pod
    )

    assert run["status"] == "COMPLETED", run
    assert run["output_data"] == {"value": 42}, (
        f"the function adds one; got {run.get('output_data')}"
    )


@scenario("A function runs isolated, in a sandbox")
@proves("PS-FUNC-002")
@covers("function.run")
async def test_a_function_runs_in_a_sandbox(pod):
    alice, the_pod = pod
    # Ask the function where it is running. A sandbox is not the host.
    probing = (
        "#input_type_name: Input\n"
        "#output_type_name: Output\n"
        "#function_name: probe\n"
        "\n"
        "import os, platform\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class Input(BaseModel):\n"
        "    value: int\n"
        "\n"
        "class Output(BaseModel):\n"
        "    system: str\n"
        "    is_container: bool\n"
        "\n"
        "async def probe(ctx, data: Input) -> Output:\n"
        "    return Output(\n"
        "        system=platform.system(),\n"
        "        is_container=os.path.exists('/.dockerenv'),\n"
        "    )\n"
    )
    function = await alice.creates_a_function(in_pod=the_pod, code=probing)

    run = await alice.runs_function(
        function["name"], with_input={"value": 0}, in_pod=the_pod
    )

    assert run["status"] == "COMPLETED", run
    assert run["output_data"]["system"] == "Linux", (
        "a function must run in the sandbox image, not on whatever host the "
        f"platform happens to be on: {run['output_data']}"
    )
    assert run["output_data"]["is_container"] is True, run["output_data"]


@scenario("An input that does not match what the function declared is refused")
@proves("PS-FUNC-001")
@covers("function.run")
async def test_a_mismatched_input_is_refused(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)

    run = await alice.runs_function(
        function["name"], with_input={"value": "not a number"}, in_pod=the_pod
    )

    assert run["status"] == "FAILED", (
        f"a function declaring `value: int` must not accept a string: {run}"
    )
    assert run.get("error"), "a failed run has to say why"


@scenario("A person changes a function's code and the next run uses it")
@proves("PS-FUNC-004")
@covers("function.update", "function.run", "function.get")
async def test_updated_code_is_what_runs_next(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)
    name = function["name"]
    before = await alice.runs_function(name, with_input={"value": 10}, in_pod=the_pod)
    assert before["output_data"] == {"value": 11}, before

    doubling = function_source(name).replace(
        "return Output(value=data.value + 1)", "return Output(value=data.value * 2)"
    )
    await alice.changes_function_code(name, to=doubling, in_pod=the_pod)

    after = await alice.runs_function(name, with_input={"value": 10}, in_pod=the_pod)
    assert after["output_data"] == {"value": 20}, after
    assert after["revision_hash"] != before["revision_hash"], (
        "a run has to be traceable to the exact code that produced it"
    )


@scenario("Every run is recorded and readable afterwards")
@proves("PS-FUNC-011")
@covers("function.run.list", "function.run.get")
async def test_runs_are_recorded(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)

    await alice.runs_function(function["name"], with_input={"value": 1}, in_pod=the_pod)
    await alice.runs_function(function["name"], with_input={"value": 2}, in_pod=the_pod)

    runs = await alice.runs_of_function(function["name"], in_pod=the_pod)
    assert len(runs) >= 2, runs
    assert all(r.get("status") for r in runs), runs


@scenario("A long function is queued and reaches a terminal state")
@proves("PS-FUNC-011")
@covers("function.run", "function.run.get")
async def test_a_job_function_completes(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod, kind="JOB")

    run = await alice.runs_function(
        function["name"], with_input={"value": 7}, in_pod=the_pod
    )

    assert run["status"] == "COMPLETED", run
    assert run["output_data"] == {"value": 8}, run


@scenario("Someone outside the pod cannot run its functions")
@proves("PS-FUNC-003")
@covers("function.run")
async def test_an_outsider_cannot_run_a_function(world, pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)
    outsider = await world.new_person("outsider")

    await outsider.is_refused_running_function(
        function["name"], with_input={"value": 1}, in_pod=the_pod
    )


class TestRunningAWorkflow:
    pytestmark = capability("Run a workflow")

    @scenario("A workflow runs its graph and reaches a result")
    @proves("PS-FLOW-001", "PS-FLOW-010")
    @covers("workflow.graph.update", "workflow.run.create", "workflow.run.get")
    async def test_a_workflow_runs_to_completion(self, pod):
        alice, the_pod = pod
        function = await alice.creates_a_function(in_pod=the_pod)
        workflow = await alice.creates_a_workflow(in_pod=the_pod)

        await alice.gives_workflow_a_graph(
            workflow["name"],
            nodes=[
                {
                    "id": "step",
                    "type": "FUNCTION",
                    "config": {
                        "function_name": function["name"],
                        "input_mapping": {"value": {"type": "literal", "value": 5}},
                    },
                },
                {"id": "done", "type": "END"},
            ],
            edges=[{"id": "e1", "source": "step", "target": "done"}],
            start={"type": "MANUAL"},
            in_pod=the_pod,
        )

        run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

        assert run["status"] == "COMPLETED", run
        assert run.get("step_history"), "a run has to record the steps it took"

    @scenario("A graph that cannot run is refused with the step at fault")
    @proves("PS-FLOW-001")
    @covers("workflow.graph.update")
    async def test_an_unrunnable_graph_is_refused(self, pod):
        alice, the_pod = pod
        workflow = await alice.creates_a_workflow(in_pod=the_pod)

        await alice.is_refused_graph(
            workflow["name"],
            nodes=[{"id": "orphan", "type": "FUNCTION", "config": {}}],
            edges=[{"id": "e1", "source": "orphan", "target": "nowhere"}],
            in_pod=the_pod,
        )

    @scenario("A workflow run is listed and readable afterwards")
    @proves("PS-FLOW-010")
    @covers("workflow.run.list_for_pod", "workflow.run.get")
    async def test_workflow_runs_are_recorded(self, pod):
        alice, the_pod = pod
        function = await alice.creates_a_function(in_pod=the_pod)
        workflow = await alice.creates_a_workflow(in_pod=the_pod)
        await alice.gives_workflow_a_graph(
            workflow["name"],
            nodes=[
                {
                    "id": "step",
                    "type": "FUNCTION",
                    "config": {
                        "function_name": function["name"],
                        "input_mapping": {"value": {"type": "literal", "value": 1}},
                    },
                },
                {"id": "done", "type": "END"},
            ],
            edges=[{"id": "e1", "source": "step", "target": "done"}],
            start={"type": "MANUAL"},
            in_pod=the_pod,
        )

        run = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

        listed = {str(r["id"]) for r in await alice.workflow_runs_in(the_pod)}
        assert str(run["id"]) in listed, listed
