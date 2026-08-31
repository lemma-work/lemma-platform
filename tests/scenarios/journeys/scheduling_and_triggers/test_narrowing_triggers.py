"""Scheduling and triggers → firing on some changes and not others.

A trigger that fires on every row is a trigger somebody turns off. Conditions
are what make one usable — and the thing that has to be right is not the firing
but the *not* firing: a skipped trigger has to be visibly skipped, distinct from
one that never arrived and one that failed. Those three look identical from
outside if the product does not say which it was, and a person debugging "why
didn't my automation run" has nowhere to look.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually, UNTIL_BACKGROUND_WORK_LANDS

pytestmark = [
    journey("Scheduling and triggers"),
    capability("React to something happening"),
]

#: What the product calls a trigger it decided not to act on.
#:
#: A filtered trigger produces no *run* — there was no work — so it is recorded
#: on the schedule as its last fire status rather than in the run history. That
#: is where a person has to look, and asking the run list instead reports
#: "nothing happened" for a feature working exactly as intended.
SKIPPED = "FILTERED"
#: A run that got past the condition, whatever became of it afterwards.
ACTED_ON = {"RECEIVED", "PROCESSING", "DISPATCHED", "COMPLETED", "TARGET_FAILED"}


@pytest.fixture
async def watching_for_done(world, run):
    """A schedule that fires only when a row's status becomes "done"."""
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title"), column("status")]
    )
    agent = await alice.creates_an_agent(in_pod=pod)
    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        agent=agent["name"],
        config={
            "table_name": table["name"],
            "operations": ["INSERT"],
            "when": {"status": {"equals": "done"}},
        },
    )
    return alice, pod, table, schedule


async def _runs(alice, schedule, pod):
    return await alice.runs_of_schedule(schedule, in_pod=pod)


@scenario("A change that does not meet the condition is skipped, and says so")
@proves("PS-SCHED-012")
@covers("schedule.create", "schedule.run.list", "record.create")
async def test_a_change_below_the_condition_is_skipped(watching_for_done):
    alice, pod, table, schedule = watching_for_done

    await alice.adds_record(
        {"title": "not finished", "status": "pending"},
        to_table=table["name"],
        in_pod=pod,
    )

    settled = await eventually(
        lambda: alice.opens_schedule(schedule, in_pod=pod),
        lambda state: state.get("last_fire_status") is not None,
        describe="the trigger to be recorded even though it was skipped",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )

    # Recorded at all is the first half of the promise: a trigger silently
    # dropped is indistinguishable from one that never arrived, and there is
    # nowhere to look when somebody asks why nothing happened.
    assert str(settled.get("last_fire_status")) == SKIPPED, (
        f"a row that did not meet the condition was not recorded as skipped: "
        f"{settled.get('last_fire_status')!r}"
    )
    # And no work was started, which is the point of the condition.
    assert not await _runs(alice, schedule, pod), (
        "the condition did not hold and the work ran anyway"
    )


@scenario("A change that meets the condition fires the work")
@proves("PS-SCHED-012", "PS-SCHED-011")
@covers("schedule.run.list", "record.create")
async def test_a_change_meeting_the_condition_fires(watching_for_done):
    alice, pod, table, schedule = watching_for_done

    await alice.adds_record(
        {"title": "finished", "status": "done"}, to_table=table["name"], in_pod=pod
    )

    acted = await eventually(
        lambda: _runs(alice, schedule, pod),
        lambda runs: any(str(run.get("status")) in ACTED_ON for run in runs),
        describe="the trigger to fire for a row that met the condition",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )
    assert acted, "a matching row triggered nothing"


@scenario("Skipped and fired are told apart in the same history")
@proves("PS-SCHED-012")
@covers("schedule.run.list")
async def test_skipped_and_fired_are_distinguishable(watching_for_done):
    alice, pod, table, schedule = watching_for_done

    await alice.adds_record(
        {"title": "one", "status": "pending"}, to_table=table["name"], in_pod=pod
    )
    await alice.adds_record(
        {"title": "two", "status": "done"}, to_table=table["name"], in_pod=pod
    )

    # The matching row is inserted second, so the last fire is the one that
    # fired — and exactly one run exists, for it. A person can therefore tell
    # "skipped" from "fired" from "never arrived": no fire status at all is the
    # third case.
    # Waiting on the run rather than on the fire status: the status is written
    # first and the run follows, so reading runs the moment the status changes
    # races the thing being asserted.
    runs = await eventually(
        lambda: _runs(alice, schedule, pod),
        lambda found: any(str(run.get("status")) in ACTED_ON for run in found),
        describe="the matching trigger to produce a run",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )
    settled = await alice.opens_schedule(schedule, in_pod=pod)

    assert len(runs) == 1, (
        f"two triggers arrived and one was meant to be skipped, so there should "
        f"be exactly one run; there are {len(runs)}: "
        f"{[r.get('status') for r in runs]}"
    )
    assert str(runs[0].get("status")) in ACTED_ON, runs
    assert str(settled.get("last_fire_status")) != SKIPPED, settled


@scenario("A condition no operation could satisfy is refused when it is written")
@proves("PS-SCHED-012")
@covers("schedule.create")
async def test_an_unsatisfiable_condition_is_refused(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title"), column("status")]
    )
    agent = await alice.creates_an_agent(in_pod=pod)

    # `changed` needs a previous value to compare against, and an INSERT has
    # none — so this can never hold. Refusing at save time is the difference
    # between a mistake found now and an automation that silently never runs.
    refused = await alice.is_refused_creating_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={
            "table_name": table["name"],
            "operations": ["INSERT"],
            "when": {"status": {"changed": True}},
        },
    )
    del agent
    assert refused, refused
