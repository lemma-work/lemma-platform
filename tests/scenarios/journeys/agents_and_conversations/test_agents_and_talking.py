"""Agents and conversations → defining an agent, and talking to it.

Proves promises in
[docs/product/journeys/agents-and-conversations.md](../../../../docs/product/journeys/agents-and-conversations.md).
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Agents and conversations"), capability("Define an agent")]


@pytest.fixture
async def pod(world):
    alice = await world.person("daniel")
    return alice, await alice.works_in("customer-support")


@scenario("A person creates an agent and gives it a job")
@proves("PS-AGENT-001")
@covers("agent.create", "agent.get", "agent.list", "agent.created")
async def test_an_agent_is_created(pod):
    alice, the_pod = pod

    agent = await alice.creates_an_agent(
        in_pod=the_pod, instruction="You triage support tickets."
    )

    reopened = await alice.opens_agent(agent["name"], in_pod=the_pod)
    assert reopened["instruction"] == "You triage support tickets."
    listed = {a["name"] for a in await alice.agents_in(the_pod)}
    assert agent["name"] in listed


@scenario("An agent name already used in the pod is refused")
@proves("PS-AGENT-001")
@covers("agent.create")
async def test_a_duplicate_agent_name_is_refused(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)

    await alice.is_refused_creating_an_agent(in_pod=the_pod, named=agent["name"])


@scenario("A person can ask what an agent is allowed to reach")
@proves("PS-AGENT-002")
@covers("agent.permissions.get")
async def test_an_agents_grants_are_readable(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)

    grants = await alice.grants_of_agent(agent["name"], in_pod=the_pod)

    assert "grants" in grants, grants


class TestTalkingToAnAgent:
    pytestmark = capability("Talk to an agent")

    @scenario("A person starts a conversation and the agent answers")
    @proves("PS-AGENT-010")
    @covers(
        "agent.conversation.create",
        "agent.conversation.message.send",
        "agent.conversation.message.list",
        "conversation.started",
    )
    async def test_a_conversation_gets_an_answer(self, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)

        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"], saying="hello"
        )
        messages = await alice.waits_for_a_reply(
            in_conversation=conversation, in_pod=the_pod
        )

        roles = [m.get("role") for m in messages]
        assert "assistant" in roles, messages

    @scenario("A conversation is readable afterwards, in order")
    @proves("PS-AGENT-010")
    @covers("agent.conversation.get", "agent.conversation.list")
    async def test_a_conversation_is_readable_afterwards(self, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)
        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"], saying="first question"
        )
        await alice.waits_for_a_reply(in_conversation=conversation, in_pod=the_pod)

        listed = await alice.conversations_in(the_pod)

        assert any(str(c["id"]) == str(conversation["id"]) for c in listed), listed
        reopened = await alice.opens_conversation(conversation, in_pod=the_pod)
        assert str(reopened["id"]) == str(conversation["id"])

    @scenario("A conversation is private to the pod")
    @proves("PS-AGENT-014")
    @covers("agent.conversation.get")
    async def test_an_outsider_cannot_read_a_conversation(self, world, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)
        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"], saying="something private"
        )

        outsider = await world.person("hannah")

        await outsider.is_refused_conversation(conversation, in_pod=the_pod)
