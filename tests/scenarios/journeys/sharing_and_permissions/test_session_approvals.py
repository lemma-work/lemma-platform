"""Sharing and permissions → approving once, and approving for a session.

"Approve for the session" is the setting people reach for when an agent keeps
asking, which makes its boundaries the thing worth testing: it has to stop the
asking inside this conversation and nowhere else. A session approval that leaks
into the next conversation, or into another person's, is a standing grant that
nobody knowingly gave.

These drive real approvals — the agent calls `request_approval`, the run pauses,
a person decides, and the backend executes the action with that person's
authority. See `harness.steps.agent.SCRIPT_KEY` for how the agent is told what
to attempt.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.agent import answers, attempts, result_of
from harness.steps.datastore import column

pytestmark = [
    journey("Sharing and permissions"),
    capability("Keep a workload inside its grant"),
]


@pytest.fixture
async def pod_with_two_records(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    first = await alice.adds_record(
        {"title": "first"}, to_table=table["name"], in_pod=pod
    )
    second = await alice.adds_record(
        {"title": "second"}, to_table=table["name"], in_pod=pod
    )
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD", "USER_INTERACTION"])
    return alice, pod, table, first, second, agent


def _delete(table: dict, record: dict) -> dict:
    return {
        "action": "delete",
        "table_name": table["name"],
        "record_id": str(record["id"]),
    }


async def _titles_in(person, table, pod) -> set[str]:
    rows = await person.records_in(table["name"], in_pod=pod)
    return {str(row.get("data", row).get("title")) for row in rows}


async def _times_asked(person, conversation, pod) -> int:
    """How many times this run stopped to ask a person for permission.

    Counted from the transcript rather than from the approvals endpoint, which
    lists what is still *pending* — after a decision it is empty, and "nobody
    was ever asked" and "everyone was answered" would look the same.
    """
    return sum(
        1
        for message in await person.messages_in(conversation, in_pod=pod)
        if message.get("tool_name") == "request_approval"
        and message.get("kind") == "TOOL_CALL"
    )


async def _was_refused(call: str, person, conversation, pod) -> bool:
    """Did the tool call named ``call`` come back needing approval?

    Reading the transcript rather than the resulting data, because the question
    here is what the *agent* was told — an attempt that was refused and an
    attempt that succeeded but changed nothing look identical from outside.
    """
    for message in await person.messages_in(conversation, in_pod=pod):
        if message.get("tool_call_id") != call:
            continue
        result = message.get("tool_result")
        if isinstance(result, dict) and result.get("needs_approval"):
            return True
    return False


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
        where_the_agent=[
            attempts(
                "request_approval",
                tool_name="pod_write_record",
                args=_delete(table, first),
                title="Delete the first row",
                reason="It is no longer needed.",
            ),
            answers("Deleted it."),
        ],
        saying="Tidy up the first row.",
    )

    [request] = await alice.waits_for_an_approval_in(conversation, in_pod=pod)
    await alice.answers_approval(
        request, allow=True, conversation=conversation, in_pod=pod
    )
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
        where_the_agent=[
            attempts(
                "request_approval",
                tool_name="pod_write_record",
                args=_delete(table, first),
                title="Delete the first row",
            ),
            answers("I was not allowed to do that."),
        ],
        saying="Tidy up the first row.",
    )

    [request] = await alice.waits_for_an_approval_in(conversation, in_pod=pod)
    await alice.answers_approval(
        request, allow=False, conversation=conversation, in_pod=pod
    )
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEV-ACCESS-002: the second approval names the same tool and args, so "
        "it takes the exact-match fast path and its permission_ids are never "
        "recorded — the agent is told yes and refused anyway, forever."
    ),
)
async def test_a_session_approval_stops_repeat_asking(pod_with_two_records):
    alice, pod, table, first, second, agent = pod_with_two_records

    del first, second

    # The real shape of this: the agent tries, is told which permission it was
    # denied, and quotes that back when it asks. An agent that invented the
    # permission list would be asking about something it was never refused.
    #
    # Reading a table needs two permissions and the check stops at the first
    # missing one, so a genuine agent is refused twice before it gets through —
    # once per permission. Both are legitimate questions. The promise is about
    # the *third* attempt: by then every permission it needs has been approved
    # for the session, and it must not be asked again.
    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts("pod_get_records", remembered_as="first", table_name=table["name"]),
            attempts(
                "request_approval",
                tool_name="pod_get_records",
                args={"table_name": table["name"]},
                title="Read the table",
                permission_ids=result_of("first", "approval.permission_ids"),
            ),
            attempts("pod_get_records", remembered_as="second", table_name=table["name"]),
            attempts(
                "request_approval",
                tool_name="pod_get_records",
                args={"table_name": table["name"]},
                title="Read the table",
                permission_ids=result_of("second", "approval.permission_ids"),
            ),
            attempts("pod_get_records", remembered_as="third", table_name=table["name"]),
            answers("Read it."),
        ],
        saying="Read that table.",
    )

    asked = await alice.answers_every_approval(
        conversation, allow=True, for_the_session=True, in_pod=pod
    )

    assert not await _was_refused("third", alice, conversation, pod), (
        "every permission the agent needs was approved for the session, and it "
        "was still refused — the approval did not carry within its own "
        "conversation"
    )
    assert asked == 2, (
        f"the person was asked {asked} times for two distinct permissions; "
        f"anything more means a session approval is not being remembered"
    )


@scenario("A session approval does not carry into the next conversation")
@proves("PS-ACCESS-022")
@covers("agent.conversation.create", "agent.conversation.approval.list")
async def test_a_session_approval_does_not_leak_to_another_conversation(
    pod_with_two_records,
):
    alice, pod, table, first, second, agent = pod_with_two_records
    del first, second

    approved = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "pod_get_records", remembered_as="denied", table_name=table["name"]
            ),
            attempts(
                "request_approval",
                tool_name="pod_get_records",
                args={"table_name": table["name"]},
                title="Read the table",
                permission_ids=result_of("denied", "approval.permission_ids"),
            ),
            answers("Read it."),
        ],
        saying="Read that table.",
    )
    [request] = await alice.waits_for_an_approval_in(approved, in_pod=pod)
    await alice.answers_approval(
        request, allow=True, for_the_session=True, conversation=approved, in_pod=pod
    )
    await alice.waits_for_the_run_to_settle(conversation=approved, in_pod=pod)

    # A new thread: same person, same agent, same table, same permission. The
    # only thing that changed is which conversation it is happening in, which
    # is precisely what a *session* approval is scoped to.
    fresh = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "pod_get_records", remembered_as="elsewhere", table_name=table["name"]
            ),
            answers("Tried."),
        ],
        saying="Read it again, in a new thread.",
    )
    await alice.waits_for_the_run_to_settle(conversation=fresh, in_pod=pod)

    assert await _was_refused("elsewhere", alice, fresh, pod), (
        "a session approval given in one conversation authorised the same "
        "action in another — that is a standing grant nobody agreed to"
    )
