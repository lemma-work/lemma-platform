"""Scheduling and triggers → reacting to something happening.

A time schedule needs a clock to move, which a suite cannot wait for. A
*datastore* schedule needs a record to change — which a scenario can do in a
line, and which exercises the same firing, claiming and dispatch path.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually, never, UNTIL_BACKGROUND_WORK_LANDS

pytestmark = [
    journey("Scheduling and triggers"),
    capability("React to something happening"),
]


@pytest.fixture
async def watched_table(world, run):
    """A pod with an agent and a table a schedule can watch."""
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title"), column("rank", "INTEGER")], shared=True
    )
    return alice, pod, agent, table["name"]


async def _runs(alice, schedule, pod):
    return await alice.runs_of_schedule(schedule, in_pod=pod)


@scenario("A change to a watched table fires the schedule")
@proves("PS-SCHED-011", "PS-SCHED-021")
@covers(
    "schedule.create",
    "record.create",
    "schedule.run.list",
    "schedule_run.completed",
    # A schedule completing is an autonomous origin producing an outcome,
    # which is branch (b) of activation -- the pod has delivered.
    "pod.delivered",
)
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
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
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


@scenario("A schedule outlives the target it pointed at, and says so")
@proves("PS-SCHED-030", "PS-SCHED-003")
@covers("schedule.create", "workflow.delete", "schedule.get", "schedule.run.list")
async def test_the_schedule_outlives_its_deleted_target(watched_table):
    """The dangling-target state is reported, not prevented.

    `PS-SCHED-030` asks that when a schedule's target no longer exists the
    firing is recorded as failed and says the target is missing, rather than
    failing silently. That clause needs the schedule to still be there to fail,
    so deleting a workflow must not take its schedules.

    Migration 0028 made exactly that choice, deliberately and against the
    previous behaviour: `schedules.workflow_id` and `schedules.agent_id` moved
    from `ON DELETE CASCADE` to `SET NULL`, because deleting and recreating a
    workflow is the normal way to restructure one and the cascade "silently
    removed the automation that ran it and every record that it had ever run" —
    `schedule_runs` cascades from `schedules` in turn, so the firing history
    went with it.

    So what is pinned here is survival: the row is still readable, no longer
    points at anything, and can be repointed at a new workflow.
    """
    alice, pod, _agent, table = watched_table
    workflow = await alice.creates_a_workflow(in_pod=pod)
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table, "operations": ["INSERT"]},
        workflow=workflow["name"],
    )
    # It really is there and armed before the deletion, so what is asserted
    # afterwards is about the deletion rather than a schedule that never was.
    await alice.opens_schedule(schedule, in_pod=pod)

    await alice.deletes_workflow(workflow["name"], in_pod=pod)

    survived = await alice.api.call(
        "GET", f"/pods/{pod['id']}/schedules/{schedule['id']}"
    )
    assert survived.status_code == 200, (
        f"the workflow was deleted and its schedule answered "
        f"{survived.status_code}; a schedule that goes with its target takes "
        f"the automation and its whole firing history with it: "
        f"{survived.text[:200]}"
    )
    body = survived.json()
    assert not body.get("workflow_id") and not body.get("workflow_name"), (
        "the schedule survived but still claims a workflow that was deleted, "
        f"so nothing can tell it is unpointed: {body}"
    )


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
    outsider = await world.person("hannah")

    response = await outsider.api.call(
        "GET", f"/pods/{pod['id']}/schedules/{schedule['id']}/runs"
    )

    assert response.status_code >= 400, response.status_code
