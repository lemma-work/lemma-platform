"""Scheduling and triggers → reacting to something happening.

A time schedule needs a clock to move, which a suite cannot wait for. A
*datastore* schedule needs a record to change — which a scenario can do in a
line, and which exercises the same firing, claiming and dispatch path.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually, never

pytestmark = [
    journey("Scheduling and triggers"),
    capability("React to something happening"),
]


@pytest.fixture
async def watched_table(world):
    """A pod with an agent and a table a schedule can watch."""
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(in_pod=pod)
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title"), column("rank", "INTEGER")], shared=True
    )
    return alice, pod, agent, table["name"]


async def _runs(alice, schedule, pod):
    return await alice.runs_of_schedule(schedule, in_pod=pod)


@scenario("A change to a watched table fires the schedule")
@proves("PS-SCHED-011", "PS-SCHED-021")
@covers("schedule.create", "record.create", "schedule.run.list",
        "schedule_run.completed")
async def test_a_record_change_fires_a_schedule(watched_table):
    alice, pod, agent, table = watched_table
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        agent=agent["name"],
    )

    await alice.adds_record({"title": "a new row", "rank": 1}, to_table=table, in_pod=pod)

    fired = await eventually(
        lambda: _runs(alice, schedule, pod),
        bool,
        describe="the datastore schedule to fire",
        timeout=60.0,
    )
    assert fired, "a watched insert must produce a firing"


@scenario("A change the schedule is not watching does not fire it")
@proves("PS-SCHED-011")
@covers("schedule.create", "record.create", "schedule.run.list")
async def test_an_unwatched_operation_does_not_fire(watched_table):
    alice, pod, agent, table = watched_table
    # Watching deletes only.
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["DELETE"]},
        agent=agent["name"],
    )

    await alice.adds_record({"title": "an insert", "rank": 1}, to_table=table, in_pod=pod)

    await never(
        lambda: _runs(alice, schedule, pod),
        bool,
        describe="a firing from an operation the schedule does not watch",
        within=8.0,
    )


@scenario("A change to a different table does not fire the schedule")
@proves("PS-SCHED-011")
@covers("schedule.create", "record.create", "schedule.run.list")
async def test_another_table_does_not_fire(watched_table):
    alice, pod, agent, table = watched_table
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        agent=agent["name"],
    )
    other = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], shared=True
    )

    await alice.adds_record({"title": "elsewhere"}, to_table=other["name"], in_pod=pod)

    await never(
        lambda: _runs(alice, schedule, pod),
        bool,
        describe="a firing from a table the schedule does not watch",
        within=8.0,
    )


@scenario("A deactivated schedule does not fire")
@proves("PS-SCHED-002")
@covers("schedule.update", "record.create", "schedule.run.list")
async def test_a_paused_schedule_does_not_fire(watched_table):
    alice, pod, agent, table = watched_table
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        agent=agent["name"],
    )
    await alice.pauses_schedule(schedule, in_pod=pod)

    await alice.adds_record({"title": "while paused"}, to_table=table, in_pod=pod)

    await never(
        lambda: _runs(alice, schedule, pod),
        bool,
        describe="a firing from a paused schedule",
        within=8.0,
    )


@scenario("A deleted schedule does not fire")
@proves("PS-SCHED-003")
@covers("schedule.delete", "record.create")
async def test_a_deleted_schedule_does_not_fire(watched_table):
    alice, pod, agent, table = watched_table
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        agent=agent["name"],
    )
    await alice.deletes_schedule(schedule, in_pod=pod)

    await alice.adds_record({"title": "after deletion"}, to_table=table, in_pod=pod)

    # The schedule is gone, so its history is gone with it; the check is that
    # nothing errors and no work is dispatched for it.
    response = await alice.api.call(
        "GET", f"/pods/{pod['id']}/schedules/{schedule['id']}/runs"
    )
    assert response.status_code >= 400 or not (response.json().get("items")), (
        f"a deleted schedule produced a firing: {response.text[:300]}"
    )


@scenario("A schedule can drive a workflow rather than an agent")
@proves("PS-SCHED-030")
@covers("schedule.create", "workflow.create", "schedule.get")
async def test_a_schedule_can_target_a_workflow(watched_table):
    alice, pod, _agent, table = watched_table
    workflow = await alice.creates_a_workflow(in_pod=pod)

    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        workflow=workflow["name"],
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert reopened.get("workflow_name") == workflow["name"], reopened


@scenario("Someone outside the pod cannot read a schedule's history")
@proves("PS-SCHED-021")
@covers("schedule.run.list")
async def test_an_outsider_cannot_read_history(world, watched_table):
    alice, pod, agent, table = watched_table
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        agent=agent["name"],
    )
    outsider = await world.new_person("outsider")

    response = await outsider.api.call(
        "GET", f"/pods/{pod['id']}/schedules/{schedule['id']}/runs"
    )

    assert response.status_code >= 400, response.status_code
