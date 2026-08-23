"""Scheduling and triggers → a schedule that cannot succeed is stopped.

Automation that fails forever is worse than automation that stops: it burns a
worker on every fire, fills the run history with noise, and — because the pod
overview hides inactive schedules — a person looking for "why did this stop"
finds the schedule gone rather than paused.

So the platform stops it after a run of failures, and has to say that is what
happened. A schedule somebody paused on purpose and one the platform gave up on
both read as inactive, and they are very different facts.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually

pytestmark = [
    journey("Scheduling and triggers"),
    capability("Know that a schedule is working"),
]

#: The product's own threshold. Matching it here rather than lowering it in the
#: stack keeps the scenario about the shipped behaviour: a deployment that
#: changes the number is still covered, and one that quietly disables the
#: breaker fails this.
FAILURES_BEFORE_STOPPING = 5


@pytest.fixture
async def doomed(world, run):
    """A schedule whose target cannot possibly succeed."""
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    workflow = await alice.creates_a_workflow(in_pod=pod)
    # A step calling a function that is not in this pod. The graph is valid —
    # the name only has to resolve when it runs — so every fire gets as far as
    # executing and then fails, which is exactly the shape the breaker is for.
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[
            {
                "id": "call",
                "type": "FUNCTION",
                "config": {"function_name": "no_such_function", "input_mapping": {}},
            },
            {"id": "done", "type": "END"},
        ],
        edges=[{"id": "call_to_done", "source": "call", "target": "done"}],
        in_pod=pod,
    )
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        workflow=workflow["name"],
        config={"table_name": table["name"], "operations": ["INSERT"]},
    )
    return alice, pod, table, schedule


@scenario("A schedule that keeps failing is stopped, and says the platform stopped it")
@proves("PS-SCHED-023")
@covers("schedule.get", "schedule.run.list", "record.create")
async def test_repeated_failure_stops_a_schedule(doomed):
    alice, pod, table, schedule = doomed

    # One fire per row, each one failing.
    for attempt in range(FAILURES_BEFORE_STOPPING + 1):
        await alice.adds_record(
            {"title": f"attempt {attempt}"}, to_table=table["name"], in_pod=pod
        )

    stopped = await eventually(
        lambda: alice.opens_schedule(schedule, in_pod=pod),
        lambda state: state.get("is_active") is False,
        describe="the schedule to give up after a run of failures",
        timeout=180.0,
    )

    assert stopped.get("paused_by_failures") is True, (
        "the schedule stopped, but reads exactly like one a person paused on "
        "purpose — so nobody can tell the platform gave up on it: "
        f"{ {k: stopped.get(k) for k in ('is_active', 'consecutive_failures', 'paused_by_failures', 'last_error')} }"
    )
    assert stopped.get("consecutive_failures") >= FAILURES_BEFORE_STOPPING, stopped


@scenario("A schedule stopped by the platform says why")
@proves("PS-SCHED-023")
@covers("schedule.get", "schedule.run.list")
async def test_a_stopped_schedule_explains_itself(doomed):
    alice, pod, table, schedule = doomed

    for attempt in range(FAILURES_BEFORE_STOPPING + 1):
        await alice.adds_record(
            {"title": f"attempt {attempt}"}, to_table=table["name"], in_pod=pod
        )

    stopped = await eventually(
        lambda: alice.opens_schedule(schedule, in_pod=pod),
        lambda state: state.get("is_active") is False,
        describe="the schedule to give up after a run of failures",
        timeout=180.0,
    )

    # "It stopped" without "here is what went wrong" leaves a person with
    # nothing to fix. The reason is in the run history rather than on the
    # schedule: `last_error` describes the *fire*, and every fire succeeded —
    # what failed each time was the work the fire started.
    assert stopped.get("consecutive_failures") >= FAILURES_BEFORE_STOPPING, stopped

    runs = await alice.runs_of_schedule(schedule, in_pod=pod)
    failed = [
        run
        for run in runs
        if str(run.get("status")) in {"TARGET_FAILED", "FAILED", "DEAD_LETTERED"}
    ]
    assert failed, (
        "the schedule was stopped for failing repeatedly and its run history "
        f"records no failure, so there is nothing to go and read: "
        f"{[r.get('status') for r in runs]}"
    )
    # `error_type` is where a schedule run says what went wrong — the target's
    # own error lives on the target's run, and this is the thread back to it.
    assert any(
        run.get("error_type") or run.get("error_code") or run.get("target_run_id")
        for run in failed
    ), f"the failed runs say what happened nowhere: {failed[:1]}"


@scenario("A person can restart a schedule the platform stopped")
@proves("PS-SCHED-023", "PS-SCHED-002")
@covers("schedule.update", "schedule.get")
async def test_a_stopped_schedule_can_be_restarted(doomed):
    alice, pod, table, schedule = doomed

    for attempt in range(FAILURES_BEFORE_STOPPING + 1):
        await alice.adds_record(
            {"title": f"attempt {attempt}"}, to_table=table["name"], in_pod=pod
        )
    await eventually(
        lambda: alice.opens_schedule(schedule, in_pod=pod),
        lambda state: state.get("is_active") is False,
        describe="the schedule to give up after a run of failures",
        timeout=180.0,
    )

    await alice.resumes_schedule(schedule, in_pod=pod)

    restarted = await alice.opens_schedule(schedule, in_pod=pod)
    assert restarted.get("is_active") is True, restarted
    # Restarting has to clear the streak, or the next single failure stops it
    # again immediately and the person cannot get out of the hole.
    assert restarted.get("paused_by_failures") is not True, (
        f"a restarted schedule still reads as stopped by the breaker: {restarted}"
    )
