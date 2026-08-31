"""Agents and conversations → asking a person, and handing work to a subagent.

Two things an agent does when a task is bigger than one turn: stop and ask, or
split the work and delegate it. Both are places where a run leaves the ordinary
path, and both have to come back to the person as part of the same conversation
rather than disappearing into a side channel.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.waiting import UNTIL_A_MODEL_ACTS
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.steps.datastore import column

pytestmark = [
    journey("Agents and conversations"),
    capability("Stay in control of what the agent does"),
]


async def _a_pod_with_an_agent(
    world, *, toolsets=("POD", "USER_INTERACTION"), instruction=None
):
    """An agent in the support pod, told how to behave.

    The instruction is the lever these scenarios pull, and it is a product one:
    telling an agent "ask before you read anything" is what a person does when
    they set one up. What the model then does with that is the thing under test.
    They used to inject the tool call directly, which proved Lemma handled *that
    call* and never that a person asking in words got there.
    """
    needs(MODEL_IS_REAL)
    alice = await world.person("daniel")
    pod = await alice.works_in("customer-support")
    agent = await alice.creates_an_agent(
        in_pod=pod,
        toolsets=list(toolsets),
        **({"instruction": instruction} if instruction else {}),
    )
    return alice, pod, agent


@scenario("An agent stops to ask a person, and carries on with their answer")
@proves("PS-AGENT-021")
@covers(
    "agent.conversation.approval.list",
    "agent.conversation.approval.resolve",
    "agent.conversation.get",
)
async def test_an_agent_asks_and_resumes_with_the_answer(world):
    alice, pod, agent = await _a_pod_with_an_agent(
        world,
        instruction=(
            "When somebody asks for a report, never choose for them. Use your "
            "question tool to ask which report they want, with the header "
            "'Report' and exactly two options: 'Weekly summary' and 'Full "
            "ledger'. Once they answer, say which one they chose."
        ),
    )

    conversation = await alice.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="Prepare a report."
    )

    [question] = await alice.waits_for_an_approval_in(conversation, in_pod=pod)
    assert question["tool_name"] == "ask_user", (
        f"the run paused on something other than the question: {question}"
    )

    await alice.api.expect(
        "POST",
        f"/pods/{pod['id']}/conversations/{conversation['id']}"
        f"/approvals/{question['tool_call_id']}/decision",
        status=200,
        what="alice answering the agent's question",
        json={"decision": "APPROVE_ONCE", "response": {"Report": "Full ledger"}},
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert "Full ledger" in transcript, (
        "the agent resumed without the answer it was given; the transcript is "
        f"{transcript[-1200:]}"
    )


@scenario("A question nobody has answered keeps the run waiting rather than guessing")
@proves("PS-AGENT-021")
@covers("agent.conversation.get", "agent.conversation.approval.list")
async def test_an_unanswered_question_keeps_waiting(world):
    alice, pod, agent = await _a_pod_with_an_agent(
        world,
        instruction=(
            "Never start work without checking first. Use your question tool "
            "to ask 'Shall I go ahead?' with the header 'Go' and two options, "
            "'Yes' and 'No', and wait for the answer before doing anything."
        ),
    )

    conversation = await alice.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="Get started."
    )

    await alice.waits_for_an_approval_in(conversation, in_pod=pod)

    # Left alone, it stays asked. An agent that times out and picks for itself
    # is worse than one that waits, because nobody finds out it chose.
    state = await alice.opens_conversation(conversation, in_pod=pod)
    assert str(state.get("status") or "").upper() not in {"COMPLETED", "FAILED"}, (
        f"the run finished without its question being answered: {state.get('status')}"
    )
    assert await alice.approvals_in(conversation, in_pod=pod), (
        "the question stopped being listed while nobody had answered it"
    )


@scenario("An agent's work is attributed to the agent, never to the person")
@proves("PS-AGENT-022")
@covers("agent.conversation.message.list", "agent.conversation.get")
async def test_agent_actions_are_attributable(world):
    alice, pod, agent = await _a_pod_with_an_agent(
        world,
        instruction=(
            "Answer questions about this pod by looking, never from memory. "
            "Always list the pod's tables before you reply."
        ),
    )
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=f"What tables are here? I am looking for {table['name']}.",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    messages = await alice.messages_in(conversation, in_pod=pod)
    mine = [m for m in messages if m.get("role") == "user"]
    its = [m for m in messages if m.get("role") in {"assistant", "tool"}]

    assert mine and its, f"this conversation has no two sides: {messages}"
    assert all(m.get("agent_run_id") for m in its), (
        "an agent's messages must say which run produced them, or nothing can "
        f"be traced back: {its}"
    )
    # The tool call is the agent's action, and it must not read as the person's.
    called = [m for m in messages if m.get("tool_name")]
    assert called, "the agent's tool call is not in the transcript at all"
    assert all(m.get("role") != "user" for m in called), (
        f"an agent's action was recorded as the person's own: {called}"
    )
    assert str(conversation["id"]) and str(agent["name"]), conversation


@scenario("An approval stays readable after the run that asked for it has finished")
@proves("PS-AGENT-022")
@covers("agent.conversation.message.list")
async def test_decisions_are_a_durable_record(world):
    alice, pod, agent = await _a_pod_with_an_agent(
        world,
        instruction=(
            "Before you read anything at all from this pod, ask the person for "
            "approval using your approval tool, and wait for their decision."
        ),
    )
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=f"Have a look at the {table['name']} table.",
    )
    [request] = await alice.waits_for_an_approval_in(conversation, in_pod=pod)
    await alice.answers_approval(
        request, allow=True, conversation=conversation, in_pod=pod
    )
    # And whatever else it goes on to ask, so the run reaches an end rather than
    # sitting on a second question this scenario is not about.
    await alice.answers_every_approval(conversation, allow=True, in_pod=pod)
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # The record of what was asked and what was decided has to outlive the run.
    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert "request_approval" in transcript, (
        "the approval disappeared from the transcript once it was decided, so "
        "there is no durable record of what was authorised"
    )
    # The *tool* the person authorised, not the wording the agent used to ask.
    # A real model titles its own request differently every time, and a scenario
    # reading the title would be measuring the model rather than the record.
    assert str(request.get("tool_name") or "") in transcript, (
        f"the transcript no longer says what was actually authorised: "
        f"{transcript[-1200:]}"
    )


@scenario("An agent hands work to a subagent and gets its result back")
@proves("PS-AGENT-030")
@covers("agent.conversation.create", "agent.conversation.get", "agent.conversation.list")
async def test_an_agent_delegates_to_a_subagent(world):
    alice, pod, agent = await _a_pod_with_an_agent(
        world,
        toolsets=("POD", "SUBAGENTS"),
        instruction=(
            "You delegate. When there is work to do, hand it to a subagent, "
            "wait for it, and report back what it found."
        ),
    )

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            "Do not count the tables yourself. Spawn a subagent, give it "
            "'Count the tables in this pod.' as its task, wait for it to "
            "finish, and tell me what it said."
        ),
    )
    await alice.waits_for_the_run_to_settle(
        conversation=conversation, in_pod=pod, timeout=UNTIL_A_MODEL_ACTS
    )

    # The parent's own transcript is where a person looks, and the delegation
    # has to be visible in it: what was handed off, and what came back.
    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert "spawn_subagent" in transcript, (
        f"the delegation is not in the parent conversation at all: {transcript[:800]}"
    )
    # That something was delegated and came back, not the exact words used to
    # delegate it — the person's instruction is a sentence, and what the agent
    # passes on is its own paraphrase.
    assert "interact_subagent" in transcript or "subagent" in transcript.lower(), (
        f"the parent conversation does not show the work coming back: "
        f"{transcript[-1200:]}"
    )

    spawned = next(
        (
            message["tool_result"]["conversation_id"]
            for message in await alice.messages_in(conversation, in_pod=pod)
            if message.get("tool_name") == "spawn_subagent"
            and isinstance(message.get("tool_result"), dict)
            and message["tool_result"].get("conversation_id")
        ),
        None,
    )
    assert spawned, f"nothing was actually spawned: {transcript[:800]}"

    # The child is a real conversation a person can open and audit. Subagent
    # threads are deliberately kept out of the pod's own list — they are part of
    # their parent, not siblings of it — so this reads it by identity.
    child = await alice.opens_conversation({"id": spawned}, in_pod=pod)
    assert str(child.get("parent_id")) == str(conversation["id"]), (
        f"the subagent's conversation is not linked to its parent: "
        f"parent_id was {child.get('parent_id')!r}"
    )
