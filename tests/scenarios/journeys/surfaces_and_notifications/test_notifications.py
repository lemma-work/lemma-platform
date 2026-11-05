"""Surfaces and notifications → being told when something needs you.

What needs a person in a pod is in one place: their inbox -- the channels with
something new for them, the lines that name them, and every question waiting
on them. A line for them alone is in their own channel with the pod.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.waiting import UNTIL_A_RUN_SETTLES, eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Be told when something needs you"),
]


@pytest.fixture
async def team(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=pod, as_role="POD_USER")
    return alice, bob, pod


async def _names(alice, person, *, pod, run, text: str):
    """Alice names ``person`` in a channel of her own: it is new to them."""
    channel = await alice.opens_a_channel(in_pod=pod, titled=run.name("launch"))
    await alice.writes_in(channel, text, in_pod=pod, naming=(person,))
    return channel


async def _a_question_for(alice, bob, *, pod):
    """A workflow that stops on Bob: its form waits on him in his inbox."""
    membership = await alice.membership_of(bob, in_pod=pod)
    workflow = await alice.creates_a_workflow(in_pod=pod)
    await alice.gives_workflow_a_graph(
        workflow["name"],
        nodes=[
            {
                "id": "approve",
                "type": "FORM",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"approved": {"type": "boolean"}},
                        "required": ["approved"],
                    },
                    "assignee_pod_member_id": membership["pod_member_id"],
                },
            },
            {"id": "done", "type": "END"},
        ],
        edges=[{"id": "approve_to_done", "source": "approve", "target": "done"}],
        in_pod=pod,
    )
    run = await alice.runs_workflow(workflow["name"], in_pod=pod)
    inbox = await eventually(
        lambda: bob.inbox_in(pod),
        lambda inbox: any(
            (item["message"].get("question") or {}).get("question", {}).get("run_id")
            == str(run["id"])
            for item in inbox["waiting"]
        ),
        describe="the form to wait on bob in his inbox",
        timeout=UNTIL_A_RUN_SETTLES,
    )
    [waiting] = [
        item
        for item in inbox["waiting"]
        if item["message"]["question"]["question"].get("run_id") == str(run["id"])
    ]
    return run, waiting


@scenario("A person has one place to see what needs them")
@proves("PS-SURF-030")
@covers(
    "channel.create",
    "channel.message.post",
    "channel.inbox.get",
    "channel.list",
)
async def test_what_names_a_person_arrives_in_their_inbox(team, run):
    alice, bob, pod = team
    before = (await bob.inbox_in(pod))["totals"]

    channel = await _names(alice, bob, pod=pod, run=run, text="@sofia review Q3?")

    totals = (await bob.inbox_in(pod))["totals"]
    assert totals["mentions"] == before["mentions"] + 1, totals
    assert totals["channels"] == before["channels"] + 1, totals
    seen = await bob.channel_seen_by_me(channel, in_pod=pod)
    assert seen["unread"]["lines"] == 1 and seen["unread"]["mentions"] == 1, seen
    assert seen["last_line"]["text"] == "@sofia review Q3?"


@scenario("What one person is told is a line in their own channel")
@proves("PS-SURF-030")
@covers("agent.notice.send", "channel.list", "channel.message.list")
async def test_a_line_for_one_person_is_in_their_own_channel(team):
    alice, bob, pod = team

    told = await alice.tells(bob, in_pod=pod, text="The Q3 numbers are in.")

    own = await bob.own_channel_in(pod)
    assert str(own["id"]) == str(told["channel_id"])
    lines = await bob.lines_in(own, in_pod=pod)
    assert any(line["text"] == "The Q3 numbers are in." for line in lines), lines
    assert all(
        line["text"] != "The Q3 numbers are in."
        for line in await alice.lines_in(await alice.own_channel_in(pod), in_pod=pod)
    ), "a line for bob turned up in alice's own channel"


@scenario("Reading a channel clears it from what is new")
@proves("PS-SURF-031")
@covers("channel.read", "channel.inbox.get", "channel.list")
async def test_reading_clears_what_is_new(team, run):
    alice, bob, pod = team
    channel = await _names(alice, bob, pod=pod, run=run, text="@sofia please look")
    before = (await bob.inbox_in(pod))["totals"]

    await bob.reads_channel(channel, in_pod=pod)

    seen = await bob.channel_seen_by_me(channel, in_pod=pod)
    unread = seen["unread"]
    assert (unread["lines"], unread["mentions"], unread["waiting"]) == (0, 0, 0), seen
    after = (await bob.inbox_in(pod))["totals"]
    assert after["mentions"] == before["mentions"] - 1, after
    assert after["channels"] == before["channels"] - 1, after


@scenario("Read state is per person, not shared")
@proves("PS-SURF-031")
@covers("channel.read", "channel.list")
async def test_read_state_is_personal(world, team, run):
    alice, bob, pod = team
    carol = await world.person("wei")
    await alice.adds(carol, to_pod=pod, as_role="POD_USER")
    channel = await alice.opens_a_channel(in_pod=pod, titled=run.name("launch"))
    await alice.writes_in(
        channel, "@sofia @wei kickoff at 3", in_pod=pod, naming=(bob, carol)
    )

    await bob.reads_channel(channel, in_pod=pod)

    assert (await bob.channel_seen_by_me(channel, in_pod=pod))["unread"]["lines"] == 0
    assert (await carol.channel_seen_by_me(channel, in_pod=pod))["unread"][
        "lines"
    ] == 1, "one person reading a channel must not clear it for another"


@scenario("A person answers a question waiting on them, and it stops asking")
@proves("PS-SURF-032")
@covers("channel.inbox.get", "channel.message.answer")
async def test_a_question_is_answered_from_the_inbox(team):
    alice, bob, pod = team
    run, waiting = await _a_question_for(alice, bob, pod=pod)

    answered = await bob.answers_question(waiting, saying={"approved": True}, in_pod=pod)

    assert answered["question"]["state"] == "ANSWERED", answered
    still = (await bob.inbox_in(pod))["waiting"]
    assert all(item["message"]["id"] != waiting["message"]["id"] for item in still)
    # The answer went where it was waiting: the run carries on.
    finished = await eventually(
        lambda: alice.opens_run(run, in_pod=pod),
        lambda state: str(state.get("status")).upper() == "COMPLETED",
        describe="the run to carry on once bob answered",
        timeout=UNTIL_A_RUN_SETTLES,
    )
    assert str(finished["status"]).upper() == "COMPLETED"


@scenario("A question already answered is not answered again")
@proves("PS-SURF-032")
@covers("channel.message.answer")
async def test_an_answered_question_refuses_a_second_answer(team):
    alice, bob, pod = team
    _run, waiting = await _a_question_for(alice, bob, pod=pod)
    await bob.answers_question(waiting, saying={"approved": True}, in_pod=pod)

    refused = await bob.is_refused_answering_question(
        waiting, saying={"approved": False}, in_pod=pod
    )

    assert refused == 409, f"a second answer was refused with {refused}, not 409"


@scenario("Someone outside the pod sees none of its inbox")
@proves("PS-SURF-030")
@covers("channel.inbox.get")
async def test_an_outsider_sees_no_inbox(world, team, run):
    alice, bob, pod = team
    await _names(alice, bob, pod=pod, run=run, text="@sofia internal")

    outsider = await world.person("hannah")

    assert await outsider.is_refused_inbox(pod) == 403


@scenario("A removed member cannot act on what was waiting on them")
@proves("PS-SURF-030")
@covers("channel.message.answer", "channel.read", "channel.inbox.get")
async def test_removal_closes_the_inbox_it_left_behind(team, run):
    """Reading was gated; answering must be too, and answering is the louder
    half: a question waiting on a person steers whatever asked it. Same rule
    as PS-POD-040: membership is the precondition, for reading and for acting
    alike."""
    alice, bob, pod = team
    channel = await _names(alice, bob, pod=pod, run=run, text="@sofia ship it?")
    _run, waiting = await _a_question_for(alice, bob, pod=pod)

    await alice.removes_member(await alice.membership_of(bob, in_pod=pod), from_pod=pod)

    # All three reported together: which of them is still open says how much
    # of the inbox a removed person keeps, and stopping at the first would hide it.
    refusals = (
        await bob.is_refused_answering_question(
            waiting, saying={"approved": True}, in_pod=pod
        ),
        await bob.is_refused_reading_channel(channel, in_pod=pod),
        await bob.is_refused_inbox(pod),
    )
    assert refusals == (403, 403, 403), (
        f"a removed member could still act on what was waiting on them: "
        f"answer={refusals[0]} read={refusals[1]} inbox={refusals[2]}"
    )
