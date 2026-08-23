"""Agents and conversations → changing an agent, and steering a conversation."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.agent import answers, attempts
from harness.steps.datastore import column

pytestmark = [journey("Agents and conversations"), capability("Define an agent")]


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


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
        outsider = await world.new_person("outsider")

        response = await outsider.api.call(
            "GET", f"/organizations/{alice.organization['id']}/agent-runtime/profiles"
        )

        assert response.status_code >= 400, response.status_code


@scenario("Granting a table is enough — the agent needs no second switch for it")
@proves("PS-AGENT-002")
@covers("agent.permissions.replace", "agent.conversation.create")
async def test_a_data_grant_brings_its_own_tools(pod):
    """This agent declares no toolsets at all.

    Pod access used to be two decisions: grant the table, then separately enable
    the pod tools. Forgetting the second failed silently — the agent could not
    see the table it had just been given. A tool no toolset exposes fails the
    run, so this passing is the derivation working.
    """
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod, toolsets=[])
    table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])
    await alice.replaces_agent_grants(
        agent["name"],
        grants=[
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": ["datastore.table.read", "datastore.record.read"],
            }
        ],
        in_pod=the_pod,
    )

    conversation = await alice.starts_a_conversation(
        in_pod=the_pod,
        with_agent=agent["name"],
        saying="What is in the table?",
        where_the_agent=[
            attempts("pod_get_records", table_name=table["name"]),
            answers("Nothing yet."),
        ],
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=the_pod)

    # The call and its return are two messages; only the return carries the
    # result, which is the half that says whether the tool actually ran.
    messages = await alice.messages_in(conversation, in_pod=the_pod)
    returned = next(
        (
            message["tool_result"]
            for message in messages
            if message.get("tool_name") == "pod_get_records"
            and isinstance(message.get("tool_result"), dict)
        ),
        None,
    )
    assert returned is not None, (
        f"the agent never got the pod tools its grant implies: {messages}"
    )
    assert returned.get("success") is True, returned


@scenario("An agent that declares nothing can still plan its work")
@proves("PS-AGENT-002")
@covers("agent.conversation.create")
async def test_the_universal_abilities_need_no_declaring(pod):
    """A task list is conversation-scoped scratch with no access implication.

    It is one of the abilities every agent simply has now, along with asking a
    person a question and requesting approval — the seam where a human gets to
    say no, which was never made safer by being optional.
    """
    alice, the_pod = pod
    agent = await alice.creates_an_agent(in_pod=the_pod, toolsets=[])

    conversation = await alice.starts_a_conversation(
        in_pod=the_pod,
        with_agent=agent["name"],
        saying="Plan something.",
        where_the_agent=[
            attempts("write_todos", todos=["- [ ] Work out what to do"]),
            answers("Planned."),
        ],
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=the_pod)

    planned = next(
        (
            message
            for message in await alice.messages_in(conversation, in_pod=the_pod)
            if message.get("tool_name") == "write_todos"
        ),
        None,
    )
    assert planned is not None, "an agent that declared nothing could not plan"
