"""Agents and conversations → staying in control of what the agent does."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Agents and conversations"), capability("Stay in control")]


@pytest.fixture
async def pod(world):
    alice = await world.person("daniel")
    return alice, await alice.works_in("customer-support")


@scenario("A pod has an agent before anyone creates one")
@proves("PS-AGENT-003")
@covers("agent.conversation.create", "agent.conversation.message.send")
async def test_a_pod_can_be_asked_without_building_an_agent(pod):
    alice, the_pod = pod

    conversation = await alice.starts_a_conversation(in_pod=the_pod, saying="hello")
    messages = await alice.waits_for_a_reply(in_conversation=conversation, in_pod=the_pod)

    assert any(m.get("role") == "assistant" for m in messages), messages


@scenario("A person stops a run and the conversation stays usable")
@proves("PS-AGENT-012")
@covers("agent.conversation.stop", "agent.conversation.get")
async def test_stopping_a_run_leaves_the_conversation_usable(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)
    conversation = await alice.starts_a_conversation(
        in_pod=the_pod, with_agent=agent["name"], saying="a question"
    )

    await alice.api.call(
        "POST", f"/pods/{the_pod['id']}/conversations/{conversation['id']}/stop"
    )

    # Whatever the run was doing, the thread is still readable and still ours.
    reopened = await alice.opens_conversation(conversation, in_pod=the_pod)
    assert str(reopened["id"]) == str(conversation["id"])


@scenario("A person can list the approvals a conversation is waiting on")
@proves("PS-AGENT-020")
@covers("agent.conversation.approval.list")
async def test_approvals_are_listable(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)
    conversation = await alice.starts_a_conversation(
        in_pod=the_pod, with_agent=agent["name"], saying="hello"
    )
    await alice.waits_for_a_reply(in_conversation=conversation, in_pod=the_pod)

    approvals = await alice.api.get(
        f"/pods/{the_pod['id']}/conversations/{conversation['id']}/approvals"
    )

    assert approvals is not None


@scenario("Deleting an agent leaves the pod still answerable")
@proves("PS-AGENT-003")
@covers("agent.delete", "agent.conversation.create")
async def test_deleting_an_agent_keeps_the_default(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)

    await alice.deletes_agent(agent["name"], in_pod=the_pod)

    conversation = await alice.starts_a_conversation(
        in_pod=the_pod, saying="still there?"
    )
    messages = await alice.waits_for_a_reply(in_conversation=conversation, in_pod=the_pod)
    assert any(m.get("role") == "assistant" for m in messages), messages


@scenario("A person adds to what the agent is already doing")
@proves("PS-AGENT-015")
@covers("agent.conversation.message.append", "agent.conversation.message.list")
async def test_a_message_sent_mid_run_is_answered(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)
    conversation = await alice.starts_a_conversation(
        in_pod=the_pod, with_agent=agent["name"], saying="start something"
    )

    # No waiting for the first turn to finish: the point is that the composer
    # takes this while the agent is still working. Whether the run is still in
    # flight by now is a race nobody can win from out here, so the assertion is
    # the promise that holds either way -- the message is accepted, and the
    # conversation answers it.
    added = await alice.adds_while_it_works(
        "and also check the invoices", in_conversation=conversation, in_pod=the_pod
    )
    assert added["conversation_id"] == str(conversation["id"])
    # Which of the two happened is reported, so a client can say so.
    assert isinstance(added["started_new_run"], bool)

    await alice.waits_for_the_run_to_settle(
        conversation=conversation, in_pod=the_pod, timeout=90.0
    )
    messages = await alice.messages_in(conversation, in_pod=the_pod)

    texts = [m.get("text") or "" for m in messages]
    assert any("check the invoices" in text for text in texts), texts
    # Accepted and then left unanswered is the failure this guards against.
    assert any(m.get("role") == "assistant" for m in messages), messages
