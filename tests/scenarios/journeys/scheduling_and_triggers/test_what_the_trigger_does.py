"""Scheduling and triggers → choosing what the trigger does.

A schedule used to answer only "when". What it woke up had to already know why:
an agent's standing instruction was the whole of its purpose, so scheduling one
meant either building an agent whose entire identity was the sentence, or
building a workflow. The pod's own assistant could not be scheduled at all —
it has no row to point a target at, and no standing instruction to interpret a
trigger with.

Both halves are one capability, so they are proved together here.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import UNTIL_BACKGROUND_WORK_LANDS, eventually

pytestmark = [
    journey("Scheduling and triggers"),
    capability("Choose what the trigger does"),
]

# What the API takes for "the assistant that answers when nobody else does".
# The same selector the conversation routes accept; it is not an agent's name,
# because the assistant does not have one.
POD_DEFAULT = "POD_DEFAULT"


@pytest.fixture
async def pod_with_agent(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    return alice, pod, agent


@scenario("A schedule carries what the work is, and reads back that way")
@proves("PS-SCHED-031")
@covers("schedule.create", "schedule.get")
async def test_a_schedule_keeps_its_instruction(pod_with_agent):
    alice, pod, agent = pod_with_agent

    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        agent=agent["name"],
        instruction="Summarise yesterday's open tickets.",
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert reopened["instruction"] == "Summarise yesterday's open tickets."


@scenario("What the work is and when to skip it are separate answers")
@proves("PS-SCHED-031")
@covers("schedule.create", "schedule.get")
async def test_the_instruction_and_the_condition_are_both_kept(pod_with_agent):
    alice, pod, agent = pod_with_agent

    schedule = await alice.api.post(
        f"/pods/{pod['id']}/schedules",
        what="creating a schedule with both an instruction and a condition",
        json={
            "name": "digest_with_a_condition",
            "schedule_type": "TIME",
            "config": {"cron": "0 9 * * *"},
            "agent_name": agent["name"],
            "instruction": "Post the digest to the team.",
            "filter_instruction": "Only when something actually changed.",
        },
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert reopened["instruction"] == "Post the digest to the team."
    assert reopened["filter_instruction"] == "Only when something actually changed."


@scenario("The instruction can be changed without rebuilding the schedule")
@proves("PS-SCHED-031")
@covers("schedule.update", "schedule.get")
async def test_the_instruction_can_be_edited(pod_with_agent):
    alice, pod, agent = pod_with_agent
    schedule = await alice.creates_a_schedule(
        in_pod=pod, agent=agent["name"], instruction="The first ask."
    )

    await alice.api.patch(
        f"/pods/{pod['id']}/schedules/{schedule['id']}",
        what="changing what a schedule asks for",
        json={"instruction": "The second ask."},
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert reopened["instruction"] == "The second ask."


@scenario("The pod's own assistant can be put on a schedule")
@proves("PS-SCHED-032")
@covers("schedule.create", "schedule.get", "schedule.list")
async def test_the_default_assistant_is_a_schedulable_target(pod_with_agent):
    alice, pod, _agent = pod_with_agent

    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        agent=POD_DEFAULT,
        instruction="Check the overnight queue and tell me what needs a person.",
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert reopened["targets_pod_default"] is True
    # No agent row exists for the assistant, so there is no id to point at —
    # the selector is echoed back in its place so the target still reads.
    assert reopened["agent_id"] is None
    assert reopened["agent_name"] == POD_DEFAULT
    listed = {str(s["id"]) for s in await alice.schedules_in(pod)}
    assert str(schedule["id"]) in listed


@scenario("Scheduling the assistant without saying what to do is refused")
@proves("PS-SCHED-032")
@covers("schedule.create")
async def test_the_assistant_needs_an_instruction(pod_with_agent):
    alice, pod, _agent = pod_with_agent

    await alice.is_refused_creating_a_schedule(
        in_pod=pod,
        config={"cron": "0 9 * * *"},
        agent=POD_DEFAULT,
    )


@scenario("Naming the assistant replaces the previous target rather than joining it")
@proves("PS-SCHED-032")
@covers("schedule.update", "schedule.get")
async def test_retargeting_to_the_assistant_replaces_the_agent(pod_with_agent):
    alice, pod, agent = pod_with_agent
    schedule = await alice.creates_a_schedule(in_pod=pod, agent=agent["name"])

    await alice.api.patch(
        f"/pods/{pod['id']}/schedules/{schedule['id']}",
        what="pointing a schedule at the default assistant",
        json={"agent_name": POD_DEFAULT, "instruction": "Take this one over."},
    )

    reopened = await alice.opens_schedule(schedule, in_pod=pod)
    assert reopened["targets_pod_default"] is True
    assert reopened["agent_id"] is None, "a schedule wakes exactly one thing"
    assert reopened["workflow_id"] is None


@scenario("A firing reaches the assistant as a conversation it answers")
@proves("PS-SCHED-032", "PS-SCHED-030")
@covers(
    "schedule.create",
    "record.create",
    "schedule.run.list",
    "agent.conversation.create",
)
async def test_a_firing_starts_a_conversation_with_the_assistant(world, run):
    """The dispatch half, end to end.

    A time schedule needs a clock the suite cannot move, so this uses a
    datastore trigger — the same claiming and dispatch path, reachable in a
    line. What it proves is that the fire lands on the assistant at all: the
    old path looked an agent row up by id and could only fail here.
    """
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], shared=True
    )

    schedule = await alice.creates_a_schedule(
        in_pod=pod,
        kind="DATASTORE",
        config={"table_name": table["name"], "operations": ["INSERT"]},
        agent=POD_DEFAULT,
        instruction="Say one sentence about the row that just arrived.",
    )

    await alice.adds_record({"title": "a new row"}, to_table=table["name"], in_pod=pod)

    fired = await eventually(
        lambda: alice.runs_of_schedule(schedule, in_pod=pod),
        bool,
        describe="the assistant's schedule to fire",
        timeout=UNTIL_BACKGROUND_WORK_LANDS,
    )
    assert fired, "a schedule targeting the assistant must actually fire"
    # Dispatched to an agent target — the assistant is one, it just has no row.
    assert fired[0]["target_kind"] == "AGENT"
