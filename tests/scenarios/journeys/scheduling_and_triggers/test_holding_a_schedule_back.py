"""Scheduling and triggers → a schedule held to a number of runs at once.

A schedule on a busy table fires once per row, and each firing starts work that
may take minutes -- an agent thinking, a person approving. Without a ceiling a
burst of rows becomes a pile of runs nobody asked for all at once. With one,
the firing that finds the schedule full starts nothing, and the history says it
was skipped and why, rather than leaving a gap a person has to explain.

The work here is a workflow that asks a person, so a run stays in flight until
the scenario answers it.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.drivers.api import UnexpectedResponse
from harness.steps.datastore import column
from harness.waiting import eventually, UNTIL_BACKGROUND_WORK_LANDS

pytestmark = [journey("Scheduling and triggers"), capability("Trust that it ran")]


@pytest.fixture
async def capped(world, run):
    """A schedule on a table, allowed one run in flight, starting a run that waits."""
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    member = await alice.membership_of(alice, in_pod=pod)
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[
            {
                "id": "ask",
                "type": "FORM",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"approved": {"type": "boolean"}},
                        "required": ["approved"],
                    },
                    "assignee_pod_member_id": member["pod_member_id"],
                },
            },
            {"id": "done", "type": "END"},
        ],
        edges=[{"id": "ask_to_done", "source": "ask", "target": "done"}],
        in_pod=pod,
    )
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        workflow=workflow["name"],
        config={
            "table_name": table["name"],
            "operations": ["INSERT"],
            "max_concurrent_runs": 1,
        },
    )
    return alice, pod, table, schedule


async def _history(alice, schedule, pod, *, settles_on, describe: str) -> list[dict]:
    return await eventually(
        lambda: alice.runs_of_schedule(schedule, in_pod=pod),
        settles_on,
        describe=describe,
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )


async def _the_run_if_there(alice, run_id, pod) -> dict | None:
    """The schedule's run, or None while it is not there to open yet.

    The history can say a firing dispatched a moment before the run it started
    answers to its id. Waiting through that is a wait like any other; anything
    but "not found" is still a failure.
    """
    try:
        return await alice.opens_run({"id": run_id}, in_pod=pod)
    except UnexpectedResponse as refused:
        if "answered 404," in str(refused):
            return None
        raise


@scenario("A firing that finds the schedule full is skipped, and says why")
@proves("PS-SCHED-024")
@covers("schedule.create", "schedule.run.list", "schedule.get", "record.create")
async def test_a_full_schedule_skips_the_firing(capped):
    alice, pod, table, schedule = capped
    await alice.adds_record({"title": "first"}, to_table=table["name"], in_pod=pod)
    await _history(
        alice,
        schedule,
        pod,
        settles_on=lambda runs: [r.get("status") for r in runs] == ["DISPATCHED"],
        describe="the first firing to start its run",
    )

    await alice.adds_record({"title": "second"}, to_table=table["name"], in_pod=pod)

    [skipped] = await eventually(
        lambda: alice.runs_of_schedule(schedule, in_pod=pod, skipped=True),
        lambda runs: len(runs) == 1,
        describe="the second firing to be recorded as skipped",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )
    assert skipped["target_run_id"] is None, f"a skipped firing started work: {skipped}"
    assert skipped["metadata"].get("skipped_because") == "max_concurrent_runs", (
        f"the skip does not say the schedule was full: {skipped}"
    )
    started = await alice.runs_of_schedule(schedule, in_pod=pod, skipped=False)
    assert len(started) == 1, f"the full schedule started more work: {started}"
    state = await alice.opens_schedule(schedule, in_pod=pod)
    assert state["consecutive_failures"] == 0, f"a skip was counted as a failure: {state}"
    assert state["is_active"] is True, state


@scenario("When the run in flight finishes, the schedule starts work again")
@proves("PS-SCHED-024")
@covers("schedule.run.list", "workflow.run.form.submit", "record.create")
async def test_a_finished_run_makes_room(capped):
    alice, pod, table, schedule = capped
    await alice.adds_record({"title": "first"}, to_table=table["name"], in_pod=pod)
    [first] = await _history(
        alice,
        schedule,
        pod,
        settles_on=lambda runs: [r.get("status") for r in runs] == ["DISPATCHED"],
        describe="the first firing to start its run",
    )
    waiting = await eventually(
        lambda: _the_run_if_there(alice, first["target_run_id"], pod),
        lambda run: run is not None and run.get("status") == "WAITING",
        describe="the scheduled run to stop on the person it asks",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )
    answered = await alice.answers_form(
        waiting, node="ask", inputs={"approved": True}, in_pod=pod
    )
    assert answered.status_code < 400, answered.text
    await _history(
        alice,
        schedule,
        pod,
        settles_on=lambda runs: [r.get("status") for r in runs] == ["COMPLETED"],
        describe="the schedule to hear that its run finished",
    )

    await alice.adds_record({"title": "next"}, to_table=table["name"], in_pod=pod)

    # Settled once the firing has handed its run over, or been turned away.
    runs = await _history(
        alice,
        schedule,
        pod,
        settles_on=lambda runs: (
            len(runs) == 2
            and {r.get("status") for r in runs} & {"DISPATCHED", "FILTERED"}
        ),
        describe="the next firing to start its run or be skipped",
    )
    assert sorted(r.get("status") for r in runs) == ["COMPLETED", "DISPATCHED"], (
        f"the schedule did not start work again once it had room: {runs}"
    )


@scenario("A schedule cannot be held to fewer than one run, or past the ceiling")
@proves("PS-SCHED-024")
@covers("schedule.update")
async def test_an_unusable_limit_is_refused(capped):
    alice, pod, table, schedule = capped

    for limit in (0, 1001):
        refusal = await alice.is_refused_changing_schedule(
            schedule,
            in_pod=pod,
            config={
                "table_name": table["name"],
                "operations": ["INSERT"],
                "max_concurrent_runs": limit,
            },
        )
        assert "max_concurrent_runs" in refusal, refusal
