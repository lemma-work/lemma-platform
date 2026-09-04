"""Automating work → running a function, and running a workflow.

The critical path of the product: code a person wrote, executing in a sandbox,
with a result they can see. Every scenario here provisions a real sandbox, so
they sit in the `sandbox` lane (`make scenarios-sandbox`).
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.building import function_source
from harness.steps.datastore import column

pytestmark = [
    journey("Automating work"),
    capability("Run a function and see what happened"),
    pytest.mark.sandbox,
]


@pytest.fixture
async def pod(world):
    alice = await world.person("daniel")
    return alice, await alice.works_in("operations")


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
    outsider = await world.person("hannah")

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

    @scenario("A step that exceeds the run's authority fails, and says so")
    @proves("PS-FLOW-014", "PS-FUNC-002")
    @covers(
        "workflow.graph.update",
        "workflow.run.create",
        "workflow.run.get",
        "table.create",
    )
    async def test_a_step_beyond_the_runs_authority_is_refused_readably(self, pod, run):
        """The unwanted clause of PS-FLOW-014, driven end to end.

        A workflow step is code, and code asks for things. What it may have is
        whatever the run's authority allows — not whatever the pod contains.
        So this gives a function no grants at all and has it read a table that
        really exists, inside a run started by somebody who can read that table
        themselves. The person's own access is not the step's access.

        Two failure modes are pinned, and they are different: the step must not
        come back with the rows (authority leaking through the run), and it
        must not be quietly skipped while the run reports success (a refusal
        swallowed, which is worse than a loud one because nothing is left to
        look at). It has to fail, and the failure has to be readable.
        """
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("secret")], named=run.name("ledger")
        )
        await alice.adds_record(
            {"secret": "not for an ungranted step"},
            to_table=table["name"],
            in_pod=the_pod,
        )

        reader = await alice.creates_a_function(
            in_pod=the_pod,
            named=run.name("peek"),
            code=(
                "#input_type_name: Input\n"
                "#output_type_name: Output\n"
                "#function_name: peek\n"
                "\n"
                "from pydantic import BaseModel\n"
                "\n"
                "class Input(BaseModel):\n"
                "    value: int\n"
                "\n"
                "class Output(BaseModel):\n"
                "    value: int\n"
                "\n"
                "async def peek(ctx, data: Input) -> Output:\n"
                # The read is the whole test: it either raises because the
                # step lacks the grant, or it returns. Nothing here depends on
                # the shape of what comes back, so a paging change cannot make
                # this scenario look like an authority failure.
                f"    ctx.pod.records.list({table['name']!r})\n"
                "    return Output(value=1)\n"
            ),
        )
        workflow = await alice.creates_a_workflow(in_pod=the_pod)
        await alice.gives_workflow_a_graph(
            workflow["name"],
            nodes=[
                {
                    "id": "step",
                    "type": "FUNCTION",
                    "config": {
                        "function_name": reader["name"],
                        "input_mapping": {"value": {"type": "literal", "value": 0}},
                    },
                },
                {"id": "done", "type": "END"},
            ],
            edges=[{"id": "e1", "source": "step", "target": "done"}],
            start={"type": "MANUAL"},
            in_pod=the_pod,
        )

        started = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

        assert str(started.get("status")).upper() != "COMPLETED", (
            f"a step read a table the function was never granted, and the run "
            f"reported success: {str(started)[:600]}"
        )
        said = str(started.get("error") or started.get("step_history") or "")
        assert "grant" in said.lower() or "permission" in said.lower(), (
            f"the step was refused, but not in words that say why or what to "
            f"do about it — which is the half of the promise that turns a "
            f"failed run into something a person can fix: {said[:400]!r}"
        )

        # The control, and the reason the refusal above means anything. Without
        # it this scenario would pass just as happily against a step that could
        # never read any table for some unrelated reason -- a broken client, a
        # bad table name, a runtime that cannot import the SDK. Granting the one
        # missing permission and watching the same graph complete is what makes
        # this a statement about authority.
        await alice.replaces_function_grants(
            reader["name"],
            grants=[
                {
                    "resource_type": "datastore_table",
                    "resource_name": table["name"],
                    # Reading rows needs the record permission as well as the
                    # table one -- the control caught that, which is the point
                    # of having a control.
                    "permission_ids": [
                        "datastore.table.read",
                        "datastore.record.read",
                    ],
                }
            ],
            in_pod=the_pod,
        )

        granted = await alice.runs_workflow(workflow["name"], in_pod=the_pod)

        assert str(granted.get("status")).upper() == "COMPLETED", (
            f"the same step still failed once the function was granted the "
            f"table, so the refusal above was not about authority: "
            f"{str(granted)[:600]}"
        )

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
