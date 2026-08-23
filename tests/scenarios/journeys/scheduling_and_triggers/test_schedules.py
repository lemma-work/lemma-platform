"""Scheduling and triggers → making work happen without being asked."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Scheduling and triggers"), capability("Make work happen on a timer")]


@pytest.fixture
async def pod_with_agent(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    return alice, pod, agent


@scenario("A person schedules work for a repeating time")
@proves("PS-SCHED-001")
@covers("schedule.create", "schedule.get", "schedule.list", "schedule.created")
async def test_a_repeating_schedule_is_created(pod_with_agent):
    alice, pod, agent = pod_with_agent

    schedule = await alice.creates_a_schedule(
        in_pod=pod, config={"cron": "0 9 * * *"}, agent=agent["name"]
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert str(reopened["id"]) == str(schedule["id"])
    listed = {str(s["id"]) for s in await alice.schedules_in(pod)}
    assert str(schedule["id"]) in listed


@scenario("Timing that cannot be interpreted is refused at creation")
@proves("PS-SCHED-001")
@covers("schedule.create")
async def test_unusable_timing_is_refused(pod_with_agent):
    alice, pod, agent = pod_with_agent

    await alice.is_refused_creating_a_schedule(
        in_pod=pod, config={"cron": "not a cron expression"}
    )


@scenario("A person pauses a schedule without losing it")
@proves("PS-SCHED-002")
@covers("schedule.update", "schedule.get")
async def test_a_schedule_can_be_paused_and_resumed(pod_with_agent):
    alice, pod, agent = pod_with_agent
    schedule = await alice.creates_a_schedule(in_pod=pod, agent=agent["name"])

    paused = await alice.pauses_schedule(schedule, in_pod=pod)
    assert paused["is_active"] is False

    resumed = await alice.resumes_schedule(schedule, in_pod=pod)
    assert resumed["is_active"] is True

    still_there = await alice.opens_schedule(schedule, in_pod=pod)
    assert str(still_there["id"]) == str(schedule["id"]), (
        "pausing must keep the definition, not discard it"
    )


@scenario("Deleting a schedule stops it and removes it from the pod")
@proves("PS-SCHED-003")
@covers("schedule.delete", "schedule.list")
async def test_deleting_a_schedule_removes_it(pod_with_agent):
    alice, pod, agent = pod_with_agent
    schedule = await alice.creates_a_schedule(in_pod=pod, agent=agent["name"])

    await alice.deletes_schedule(schedule, in_pod=pod)

    listed = {str(s["id"]) for s in await alice.schedules_in(pod)}
    assert str(schedule["id"]) not in listed


@scenario("A person can see a schedule's firing history")
@proves("PS-SCHED-021")
@covers("schedule.run.list")
async def test_a_schedules_history_is_readable(pod_with_agent):
    alice, pod, agent = pod_with_agent
    schedule = await alice.creates_a_schedule(in_pod=pod, agent=agent["name"])

    runs = await alice.runs_of_schedule(schedule, in_pod=pod)

    assert isinstance(runs, list), runs


@scenario("Someone outside the pod cannot create or see its schedules")
@proves("PS-SCHED-001")
@covers("schedule.create", "schedule.list")
async def test_an_outsider_cannot_touch_schedules(world, pod_with_agent):
    alice, pod, agent = pod_with_agent
    await alice.creates_a_schedule(in_pod=pod, agent=agent["name"])
    outsider = await world.person("hannah")

    created = await outsider.api.call(
        "POST",
        f"/pods/{pod['id']}/schedules",
        json={"name": "trespass", "schedule_type": "TIME", "config": {"cron": "0 9 * * *"}},
    )
    listed = await outsider.api.call("GET", f"/pods/{pod['id']}/schedules")

    assert created.status_code >= 400, created.status_code
    assert listed.status_code >= 400, listed.status_code
