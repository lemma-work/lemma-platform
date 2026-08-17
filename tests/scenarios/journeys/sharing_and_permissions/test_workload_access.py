"""Sharing and permissions → what an agent may do, and for how long.

The scenarios here are about *workloads* rather than people: an agent acting in
someone's name is the sharpest edge in the product, because it is the one actor
that keeps working after the person has walked away.

Each of these drives a real agent run. The stack boots its agents on a
deterministic model, and that model takes its turns from the conversation's own
`metadata` — so a scenario says what the agent *attempts* and Lemma's real
authorization, approval and tool dispatch decide what happens. Nothing is
patched; see `harness.steps.agent.SCRIPT_KEY`.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.agent import attempts
from harness.steps.datastore import column

pytestmark = [
    journey("Sharing and permissions"),
    capability("Keep a workload inside its grant"),
]


@pytest.fixture
async def pod_with_a_record(world):
    """A pod, a table, and one record in it that a scenario can try to destroy."""
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    record = await alice.adds_record(
        {"title": "the one that must survive"}, to_table=table["name"], in_pod=pod
    )
    return alice, pod, table, record


async def _still_there(person, *, record, table, pod) -> None:
    rows = await person.records_in(table["name"], in_pod=pod)
    if not any(str(r["id"]) == str(record["id"]) for r in rows):
        raise AssertionError(
            "the record was deleted by an agent that was never granted deletion "
            "and never approved for it"
        )


@scenario("An agent cannot destroy anything it was not granted or approved for")
@proves("PS-ACCESS-021")
@covers("agent.create", "agent.conversation.create", "record.delete", "record.list")
async def test_an_ungranted_agent_cannot_delete_a_record(pod_with_a_record):
    alice, pod, table, record = pod_with_a_record
    # Created agents hold only what they were granted (PS-AGENT-002). This one
    # was granted nothing, and its owner is the pod admin who made the table —
    # which is the case the promise singles out.
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD"])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "pod_write_record",
                action="delete",
                table_name=table["name"],
                record_id=str(record["id"]),
            )
        ],
        saying="Delete that row.",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    await _still_there(alice, record=record, table=table, pod=pod)


@scenario("A destructive attempt comes back to a person rather than being dropped")
@proves("PS-ACCESS-021", "PS-AGENT-020")
@covers("agent.conversation.approval.list", "agent.conversation.get")
async def test_a_destructive_attempt_asks_rather_than_failing_silently(
    pod_with_a_record,
):
    alice, pod, table, record = pod_with_a_record
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD"])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "pod_write_record",
                action="delete",
                table_name=table["name"],
                record_id=str(record["id"]),
            )
        ],
        saying="Delete that row.",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # Refusing is not enough on its own: a refusal nobody can act on is the same
    # as the feature not working. The run has to leave a person something to
    # decide, or say plainly that it was refused.
    requests = await alice.approvals_in(conversation, in_pod=pod)
    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert requests or "approval" in transcript.lower(), (
        "the agent's destructive attempt neither asked for approval nor "
        f"reported being refused; transcript was {transcript[:800]}"
    )


@scenario("An agent can only reach the connectors it was granted")
@proves("PS-CONN-033", "PS-AGENT-002")
@covers("agent.create", "agent.conversation.create", "connector.operation.execute")
async def test_an_agent_cannot_call_an_ungranted_connector(world, provider):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url,
        spec_url=provider.spec_url,
    )
    await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        credentials={"access_token": "alice-provider-token"},
    )
    pod = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["CONNECTORS"])
    provider.clear()

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "run_connector_operation",
                auth_config=auth_config["name"],
                operation="listWidgets",
                arguments={},
            )
        ],
        saying="Fetch the widgets.",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # The strongest possible statement: nothing reached the provider at all.
    # An assertion on the agent's own words would pass just as well if the call
    # went out and the answer was merely worded as a refusal.
    assert not provider.received, (
        "an agent with no connector grant reached the provider anyway: "
        f"{[call.path for call in provider.received]}"
    )


@scenario("Removing a person stops the agents working in their name")
@proves("PS-ACCESS-023")
@covers("pod.member.remove", "agent.conversation.get", "agent.conversation.message.send")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEV-ACCESS-001: the pod closes but the conversation does not — a "
        "removed member can still read the thread and still send the agent new "
        "instructions, which it executes with its grants."
    ),
)
async def test_removing_a_person_stops_their_delegations(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))
    await alice.adds(bob, to_pod=pod, as_role="POD_EDITOR")

    # Bob delegates work to an agent, the way anyone would.
    agent = await bob.creates_an_agent(in_pod=pod, toolsets=["POD"])
    conversation = await bob.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="Have a look at the tables."
    )
    await bob.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    await alice.removes_member(
        await alice.membership_of(bob, in_pod=pod), from_pod=pod
    )

    # Removal itself worked: the pod is closed to him (PS-POD-040).
    await bob.is_refused_pod(pod)

    # So the delegation inside that pod must be closed too — both to read and
    # to extend. Both are checked and both reported: "can still read" and "can
    # still act" are different sizes of problem, and stopping at the first
    # would hide which one this is.
    read = await bob.api.call(
        "GET", f"/pods/{pod['id']}/conversations/{conversation['id']}"
    )
    wrote = await bob.api.call(
        "POST",
        f"/pods/{pod['id']}/conversations/{conversation['id']}/messages",
        json={"content": "Carry on without me."},
    )
    assert read.status_code >= 400 and wrote.status_code >= 400, (
        f"a removed member still reaches their delegation: reading it answered "
        f"{read.status_code}, driving it answered {wrote.status_code}"
    )
