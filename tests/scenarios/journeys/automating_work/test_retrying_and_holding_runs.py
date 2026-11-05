"""Automating work → retrying a run where it failed, and holding runs back.

Two things a person reaches for once a workflow does real work. A run that
failed late should not have to redo everything before the step that broke --
especially when that is a person's answer or an agent's paid-for work. And a
workflow that many things can start needs a ceiling on how many of its runs are
going at once, without anything being refused or lost on the way.

Most of these need no sandbox: a step calling a function the pod does not have
fails at run time, which is all a retry needs to fail at, and a step asking a
person holds a run in progress for as long as the scenario wants. The one that
fixes the function and watches the retry succeed creates a function, so it is
in the sandbox lane.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually, UNTIL_A_RUN_SETTLES, UNTIL_BACKGROUND_WORK_LANDS

pytestmark = [journey("Automating work"), capability("Run a workflow")]

SETTLED = {"COMPLETED", "FAILED", "CANCELLED"}


def _asks(member_id: str) -> dict:
    return {
        "id": "ask",
        "type": "FORM",
        "config": {
            "input_schema": {
                "type": "object",
                "properties": {"approved": {"type": "boolean"}},
                "required": ["approved"],
            },
            "assignee_pod_member_id": member_id,
        },
    }


def _calls(function_name: str) -> dict:
    return {
        "id": "call",
        "type": "FUNCTION",
        "config": {
            "function_name": function_name,
            "input_mapping": {"value": {"type": "literal", "value": 1}},
        },
    }


DONE = {"id": "done", "type": "END"}


def _failing(name: str, *, fixed: bool) -> str:
    body = (
        "return Output(value=data.value + 1)" if fixed else "raise ValueError('not yet')"
    )
    return (
        "#input_type_name: Input\n"
        "#output_type_name: Output\n"
        f"#function_name: {name}\n"
        "\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class Input(BaseModel):\n"
        "    value: int\n"
        "\n"
        "class Output(BaseModel):\n"
        "    value: int\n"
        "\n"
        f"async def {name}(ctx, data: Input) -> Output:\n"
        f"    {body}\n"
    )


async def _until(alice, run, pod, *, statuses: set[str], describe: str) -> dict:
    return await eventually(
        lambda: alice.opens_run(run, in_pod=pod),
        lambda state: str(state.get("status")) in statuses,
        describe=describe,
        timeout=UNTIL_A_RUN_SETTLES,
    )


@pytest.fixture
async def team(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    member = await alice.membership_of(alice, in_pod=pod)
    return alice, pod, member["pod_member_id"]


@scenario("A retried run redoes only the step that failed, not the person's answer")
@proves("PS-FLOW-016")
@covers("workflow.run.retry", "workflow.run.get", "workflow.run.form.submit")
async def test_a_retry_starts_at_the_failed_step(team):
    alice, pod, member_id = team
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[_asks(member_id), _calls("no_such_function"), DONE],
        edges=[
            {"id": "ask_to_call", "source": "ask", "target": "call"},
            {"id": "call_to_done", "source": "call", "target": "done"},
        ],
        in_pod=pod,
    )
    waiting = await alice.runs_workflow(workflow["name"], in_pod=pod)
    assert waiting["status"] == "WAITING", waiting
    answered = await alice.answers_form(
        waiting, node="ask", inputs={"approved": True}, in_pod=pod
    )
    assert answered.status_code < 400, answered.text
    failed = await _until(
        alice, waiting, pod, statuses=SETTLED, describe="the run to fail at its call"
    )
    assert failed["status"] == "FAILED" and failed["failed_node_id"] == "call", failed

    retried = await alice.retries_run(failed, in_pod=pod)

    # The function is still missing, so the retry fails the same way -- but
    # from the step that failed. Had it started over, it would be waiting on
    # the person again.
    assert retried["status"] == "FAILED", retried
    assert retried["failed_node_id"] == "call", retried
    steps = [(step["node_id"], step["status"]) for step in retried["step_history"]]
    assert [node for node, _ in steps].count("ask") == 1, (
        f"the person was asked again on a retry of a later step: {steps}"
    )
    calls = [status for node, status in steps if node == "call"]
    assert len(calls) == 2, f"the failed attempt and the retry are not both kept: {steps}"
    assert retried["execution_context"]["ask"] == {"approved": True}, (
        "the answer given before the failure was not kept for the retry"
    )


@scenario("A run that has not failed cannot be retried")
@proves("PS-FLOW-016")
@covers("workflow.run.retry")
async def test_only_a_failed_run_can_be_retried(team):
    alice, pod, member_id = team
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[_asks(member_id), DONE],
        edges=[{"id": "ask_to_done", "source": "ask", "target": "done"}],
        in_pod=pod,
    )
    waiting = await alice.runs_workflow(workflow["name"], in_pod=pod)

    await alice.is_refused_retrying_run(waiting, in_pod=pod)

    await alice.answers_form(waiting, node="ask", inputs={"approved": True}, in_pod=pod)
    finished = await _until(
        alice, waiting, pod, statuses=SETTLED, describe="the answered run to finish"
    )
    assert finished["status"] == "COMPLETED", finished
    await alice.is_refused_retrying_run(finished, in_pod=pod)


@pytest.mark.sandbox
@scenario(
    "Once the broken step is fixed, a retry finishes the run and its schedule says so"
)
@proves("PS-FLOW-016")
@covers("workflow.run.retry", "workflow.run.get", "schedule.run.list", "record.create")
async def test_a_retry_after_a_fix_finishes_the_run(team, run):
    alice, pod, _member_id = team
    name = run.name("convert").replace("-", "_")
    function = await alice.creates_a_function(
        in_pod=pod, named=name, code=_failing(name, fixed=False)
    )
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[_calls(function["name"]), DONE],
        edges=[{"id": "call_to_done", "source": "call", "target": "done"}],
        in_pod=pod,
    )
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        workflow=workflow["name"],
        config={"table_name": table["name"], "operations": ["INSERT"]},
    )
    await alice.adds_record({"title": "first"}, to_table=table["name"], in_pod=pod)
    [firing] = await eventually(
        lambda: alice.runs_of_schedule(schedule, in_pod=pod),
        lambda runs: len(runs) == 1 and runs[0].get("status") == "TARGET_FAILED",
        describe="the scheduled run to fail",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )

    await alice.changes_function_code(
        function["name"], to=_failing(function["name"], fixed=True), in_pod=pod
    )
    retried = await alice.retries_run({"id": firing["target_run_id"]}, in_pod=pod)

    assert retried["status"] == "COMPLETED", retried
    await eventually(
        lambda: alice.runs_of_schedule(schedule, in_pod=pod),
        lambda runs: [r.get("status") for r in runs] == ["COMPLETED"],
        describe="the schedule's history to report the retried run's success",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )


class TestHoldingRunsBack:
    @scenario("A run started past a workflow's limit waits its turn, then runs")
    @proves("PS-FLOW-017")
    @covers("workflow.create", "workflow.run.create", "workflow.run.get")
    async def test_a_run_past_the_limit_is_queued_not_refused(self, team):
        alice, pod, member_id = team
        workflow = await alice.creates_a_workflow(in_pod=pod, at_most_at_once=1)
        await alice.gives_workflow_a_graph(
            workflow["name"],
            nodes=[_asks(member_id), DONE],
            edges=[{"id": "ask_to_done", "source": "ask", "target": "done"}],
            in_pod=pod,
        )
        first = await alice.runs_workflow(workflow["name"], in_pod=pod)
        assert first["status"] == "WAITING", first

        second = await alice.starts_workflow(workflow["name"], in_pod=pod)

        assert second["status"] not in {"FAILED", "CANCELLED"}, second
        assert (second.get("active_wait") or {}).get("wait_type") == "QUEUED", (
            f"a run past the limit is not shown as queued: {second}"
        )

        await alice.answers_form(first, node="ask", inputs={"approved": True}, in_pod=pod)
        admitted = await _until(
            alice,
            second,
            pod,
            statuses={"WAITING", *SETTLED},
            describe="the queued run to start once the first finished",
        )
        assert admitted["status"] == "WAITING", admitted
        assert admitted["active_wait"]["wait_type"] != "QUEUED", admitted

    @scenario("A workflow cannot be limited to fewer than one run")
    @proves("PS-FLOW-017")
    @covers("workflow.update")
    async def test_a_limit_below_one_is_refused(self, team):
        alice, pod, _member_id = team
        workflow = await alice.creates_a_workflow(in_pod=pod)

        await alice.is_refused_changing_workflow(
            workflow["name"], in_pod=pod, max_concurrent_runs=0
        )
