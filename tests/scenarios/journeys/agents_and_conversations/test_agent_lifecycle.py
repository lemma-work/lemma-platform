"""Agents and conversations → changing an agent, and steering a conversation."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import OPEN_SIGNUP
from harness.steps.datastore import column

pytestmark = [journey("Agents and conversations"), capability("Define an agent")]


@pytest.fixture
async def pod(world):
    alice = await world.person("daniel")
    return alice, await alice.works_in("customer-support")


@scenario("A person changes what an agent does")
@proves("PS-AGENT-001")
@covers("agent.update", "agent.get")
async def test_an_agent_can_be_changed(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(
        in_pod=the_pod, instruction="You answer questions."
    )

    await alice.changes_agent(
        agent["name"], in_pod=the_pod, instruction="You triage tickets instead."
    )

    reopened = await alice.opens_agent(agent["name"], in_pod=the_pod)
    assert reopened["instruction"] == "You triage tickets instead.", reopened


@scenario("A person grants an agent access to one table")
@proves("PS-AGENT-002", "PS-ACCESS-020")
@covers("agent.permissions.replace", "agent.permissions.get")
async def test_an_agents_reach_can_be_set(pod):
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod)
    table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])

    await alice.replaces_agent_grants(
        agent["name"],
        grants=[{
            "resource_type": "datastore_table",
            "resource_name": table["name"],
            "permission_ids": ["datastore.table.read", "datastore.record.read"],
        }],
        in_pod=the_pod,
    )

    held = await alice.grants_of_agent(agent["name"], in_pod=the_pod)
    assert table["name"] in str(held), held


@scenario("A person grants a function access to one table")
@proves("PS-FUNC-003", "PS-ACCESS-020")
@covers("function.permissions.replace", "function.permissions.get")
@pytest.mark.sandbox
async def test_a_functions_reach_can_be_set(pod):
    alice, the_pod = pod
    function = await alice.creates_a_function(in_pod=the_pod)
    table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])

    await alice.replaces_function_grants(
        function["name"],
        grants=[{
            "resource_type": "datastore_table",
            "resource_name": table["name"],
            "permission_ids": ["datastore.table.read"],
        }],
        in_pod=the_pod,
    )

    held = await alice.grants_of_function(function["name"], in_pod=the_pod)
    assert table["name"] in str(held), held


class TestSteeringAConversation:
    pytestmark = capability("Talk to an agent")

    @scenario("A person retitles a conversation")
    @proves("PS-AGENT-010")
    @covers("agent.conversation.update", "agent.conversation.get")
    async def test_a_conversation_can_be_retitled(self, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)
        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"]
        )

        await alice.renames_conversation(
            conversation, to="Q3 billing questions", in_pod=the_pod
        )

        reopened = await alice.opens_conversation(conversation, in_pod=the_pod)
        assert reopened["title"] == "Q3 billing questions", reopened

    @scenario("A person watches a conversation as it happens")
    @proves("PS-AGENT-011")
    @covers("agent.conversation.stream")
    async def test_a_conversation_can_be_watched(self, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)
        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"], saying="hello"
        )

        status, content_type, _first = await alice.watches(conversation, in_pod=the_pod)

        assert status == 200, status
        assert "text/event-stream" in content_type, (
            f"a watcher needs a live stream, not a single response: {content_type}"
        )

    @scenario("Retrying a run that did not fail is refused")
    @proves("PS-AGENT-013")
    @covers("agent.conversation.retry")
    async def test_retrying_a_healthy_run_is_refused(self, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)
        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"], saying="hello"
        )
        await alice.waits_for_a_reply(in_conversation=conversation, in_pod=the_pod)

        response = await alice.retries(conversation, in_pod=the_pod)

        assert response.status_code >= 400, (
            f"a completed run should not be retryable ({response.status_code})"
        )

    @scenario("Deciding an approval that does not exist is refused")
    @proves("PS-AGENT-020")
    @covers("agent.conversation.approval.resolve", "agent.conversation.approval.list")
    async def test_deciding_an_unknown_approval_is_refused(self, pod):
        alice, the_pod = pod
        agent = await alice.creates_an_agent(in_pod=the_pod)
        conversation = await alice.starts_a_conversation(
            in_pod=the_pod, with_agent=agent["name"], saying="hello"
        )
        await alice.waits_for_a_reply(in_conversation=conversation, in_pod=the_pod)

        assert await alice.approvals_in(conversation, in_pod=the_pod) == [], (
            "nothing in this conversation asked for approval"
        )
        response = await alice.decides(
            {"id": "00000000-0000-0000-0000-000000000001"},
            allow=True, conversation=conversation, in_pod=the_pod,
        )

        assert response.status_code >= 400, (
            f"an approval that does not exist must not be decidable "
            f"({response.status_code})"
        )


class TestModelProfiles:
    pytestmark = capability("Choose which model an agent uses")

    @scenario("An organization sees the model profiles available to it")
    @proves("PS-AGENT-004")
    @covers("agent.runtime.profiles.list")
    async def test_runtime_profiles_are_listable(self, pod):
        alice, _the_pod = pod

        profiles = await alice.runtime_profiles_in(alice.organization)

        assert isinstance(profiles, list), profiles

    @scenario("Someone outside the organization cannot see its model profiles")
    @proves("PS-AGENT-004")
    @covers("agent.runtime.profiles.list")
    async def test_an_outsider_cannot_see_profiles(self, world, pod):
        alice, _the_pod = pod
        # Somebody in no organization at all, which is what this promise is
        # about. None of the standing cast is that, so this one stays a
        # fresh person and says so.
        needs(OPEN_SIGNUP)
        outsider = await world.new_person("outsider")

        response = await outsider.api.call(
            "GET", f"/organizations/{alice.organization['id']}/agent-runtime/profiles"
        )

        assert response.status_code >= 400, response.status_code
