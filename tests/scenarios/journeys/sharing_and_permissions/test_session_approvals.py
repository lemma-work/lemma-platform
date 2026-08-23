"""Sharing and permissions → approving once, and approving for a session.

"Approve for the session" is the setting people reach for when an agent keeps
asking, which makes its boundaries the thing worth testing: it has to stop the
asking inside this conversation and nowhere else. A session approval that leaks
into the next conversation, or into another person's, is a standing grant that
nobody knowingly gave.

These drive real approvals on a real model: the agent is *told* to ask before it
changes anything — which is what a person setting one up does — the run pauses,
a person decides, and the backend executes the action with that person's
authority. Asserted on effect, never on the agent's words: whether the row is
still there, whether the run came back to life, whether the person was asked
again. What a model says about any of that is its own business and changes every
time it is asked.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.steps.datastore import column

pytestmark = [
    journey("Sharing and permissions"),
    capability("Keep a workload inside its grant"),
]


@pytest.fixture
async def pod_with_two_records(world, run):
    needs(MODEL_IS_REAL)
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("approvals"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    first = await alice.adds_record(
        {"title": "first"}, to_table=table["name"], in_pod=pod
    )
    second = await alice.adds_record(
        {"title": "second"}, to_table=table["name"], in_pod=pod
    )
    agent = await alice.creates_an_agent(
        in_pod=pod,
        toolsets=["POD", "USER_INTERACTION"],
        # Told to ask, rather than made to. This is the setting a person reaches
        # for when they want an agent that checks before it changes anything,
        # and it is the product feature these scenarios are about.
        instruction=(
            "You may read this pod freely. Before you change or delete "
            "anything, always ask the person for approval with your approval "
            "tool and wait for their decision. Never delete anything you were "
            "not asked to."
        ),
    )
    try:
        yield alice, pod, table, first, second, agent
    finally:
        await alice.deletes_pod(pod)


def _delete(table: dict, record: dict) -> dict:
    return {
        "action": "delete",
        "table_name": table["name"],
        "record_id": str(record["id"]),
    }


async def _titles_in(person, table, pod) -> set[str]:
    rows = await person.records_in(table["name"], in_pod=pod)
    return {str(row.get("data", row).get("title")) for row in rows}


@scenario("Approving an action runs exactly the action that was described")
@proves("PS-AGENT-020")
@covers(
    "agent.conversation.approval.list",
    "agent.conversation.approval.resolve",
    "record.delete",
)
async def test_approving_runs_the_described_action(pod_with_two_records):
    alice, pod, table, first, second, agent = pod_with_two_records

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row titled 'first' from the "
            f"{table['name']} table. Leave everything else alone."
        ),
    )

    # Every approval, not the first: an agent told to ask before it changes
    # anything asks before *each* thing, and how many that turns out to be is
    # the model's business rather than the product's.
    await alice.answers_every_approval(conversation, allow=True, in_pod=pod)
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    remaining = await _titles_in(alice, table, pod)
    assert remaining == {"second"}, (
        f"approving deleted the wrong thing, or nothing: {remaining}"
    )


@scenario("Denying an action leaves it undone and lets the agent say so")
@proves("PS-AGENT-020")
@covers("agent.conversation.approval.resolve", "agent.conversation.message.list")
async def test_denying_leaves_the_action_undone(pod_with_two_records):
    alice, pod, table, first, second, agent = pod_with_two_records
    del second

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row titled 'first' from the "
            f"{table['name']} table. Leave everything else alone."
        ),
    )

    await alice.answers_every_approval(conversation, allow=False, in_pod=pod)
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    assert await _titles_in(alice, table, pod) == {"first", "second"}, (
        "a denied action was carried out anyway"
    )
    # The run has to come back to life rather than sitting denied forever.
    conversation = await alice.opens_conversation(conversation, in_pod=pod)
    assert str(conversation.get("status") or "").upper() != "WAITING_FOR_INPUT", (
        "the conversation is still waiting after the person answered"
    )


@scenario("Approving for the session stops the asking inside that conversation")
@proves("PS-ACCESS-022")
@covers("agent.conversation.approval.resolve", "agent.conversation.approval.list")
async def test_a_session_approval_stops_repeat_asking(pod_with_two_records):
    alice, pod, table, first, second, agent = pod_with_two_records
    del first, second, agent

    # An agent with the pod tools and *no grants at all*, and deliberately not
    # told to ask for anything. Being refused is what raises the approval here,
    # and a session approval is scoped to that refusal — an agent instructed to
    # always ask would ask again whatever the session said, and this scenario
    # would be measuring the instruction rather than the product.
    #
    # Reading a table needs two permissions and the check stops at the first
    # missing one, so it is refused once per permission before it gets through.
    # Both are legitimate questions. What must not happen is being asked again
    # for a permission already approved for this session.
    ungranted = await alice.creates_an_agent(
        in_pod=pod,
        toolsets=["POD", "USER_INTERACTION"],
        instruction="Do what you are asked, using the pod tools available to you.",
    )

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=ungranted["name"],
        saying=(
            f"Read the {table['name']} table and list its rows. Then read it "
            f"again and tell me whether anything changed between the two reads."
        ),
    )

    asked = await alice.answers_every_approval(
        conversation, allow=True, for_the_session=True, in_pod=pod
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # Two reads, and at most one question per distinct permission. More than
    # that means a session approval was not remembered and the person was asked
    # again for something they had already settled.
    assert 1 <= asked <= 2, (
        f"the person was asked {asked} times inside one conversation while "
        f"approving for the session. Reading a table needs two permissions, so "
        f"two questions is the ceiling — anything above it is the same "
        f"permission being asked for twice"
    )


@scenario("A session approval does not carry into the next conversation")
@proves("PS-ACCESS-022")
@covers("agent.conversation.create", "agent.conversation.approval.list")
async def test_a_session_approval_does_not_leak_to_another_conversation(
    pod_with_two_records,
):
    alice, pod, table, first, second, agent = pod_with_two_records

    approved = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row titled 'first' from the "
            f"{table['name']} table."
        ),
    )
    [request] = await alice.waits_for_an_approval_in(approved, in_pod=pod)
    await alice.answers_approval(
        request, allow=True, for_the_session=True, conversation=approved, in_pod=pod
    )
    await alice.waits_for_the_run_to_settle(conversation=approved, in_pod=pod)

    # A new thread: same person, same agent, same table, same kind of change.
    # The only thing that changed is which conversation it is happening in,
    # which is precisely what a *session* approval is scoped to.
    fresh = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row titled 'second' from the "
            f"{table['name']} table."
        ),
    )

    asked_again = await alice.waits_for_an_approval_in(fresh, in_pod=pod)

    assert asked_again, (
        "a session approval given in one conversation authorised the same kind "
        "of change in another without asking — that is a standing grant nobody "
        "agreed to"
    )
    # And until somebody answers it, the second row is still there.
    assert "second" in await _titles_in(alice, table, pod), (
        "the change went through in a conversation that had no approval for it"
    )
