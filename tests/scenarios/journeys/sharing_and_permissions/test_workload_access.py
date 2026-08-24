"""Sharing and permissions → what an agent may do, and for how long.

The scenarios here are about *workloads* rather than people: an agent acting in
someone's name is the sharpest edge in the product, because it is the one actor
that keeps working after the person has walked away.

Each of these drives a real agent run, on whatever model the deployment is
configured with, and says what a person would say. They used to inject the tool
call they wanted to see — which proved that Lemma refused *that call*, and never
that a sentence a person actually typed ended up refused. So they ask in words
and assert on what must be true afterwards: the record is still there, and the
person was told something.

The cost is that they need a model that can think, and skip with a reason where
there is not one. That is the honest trade: an agent scenario driven by a
scripted turn on a deterministic model is not evidence about the product a
person uses.
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
async def pod_with_a_record(world, run):
    """A pod, a table, and one record in it that a scenario can try to destroy."""
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("workload"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    record = await alice.adds_record(
        {"title": "the one that must survive"}, to_table=table["name"], in_pod=pod
    )
    try:
        yield alice, pod, table, record
    finally:
        await alice.deletes_pod(pod)


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
    needs(MODEL_IS_REAL)
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD"])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row titled {record['title']!r} from the "
            f"{table['name']} table. Go ahead and do it."
        ),
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
    needs(MODEL_IS_REAL)
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD"])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row titled {record['title']!r} from the "
            f"{table['name']} table. Go ahead and do it."
        ),
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # Two things have to hold, and the second is the one with teeth. The row is
    # still there — nothing was destroyed on the quiet — and the person was
    # answered, so the request did not simply vanish. Silence is the failure
    # this promise is about: a refusal nobody is told about is the same, to the
    # person waiting, as the feature not working.
    await _still_there(alice, record=record, table=table, pod=pod)

    requests = await alice.approvals_in(conversation, in_pod=pod)
    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert requests or transcript.strip(), (
        "the agent's destructive attempt left the person nothing at all — no "
        "approval to decide and nothing said. Deliberately not asserting on the "
        "wording of the refusal: a real model phrases it differently every "
        "time, and a test that reads the words would be measuring the model "
        "rather than the product"
    )


@scenario("An agent can only reach the connectors it was granted")
@proves("PS-CONN-033", "PS-AGENT-002")
@covers("agent.create", "agent.conversation.create", "connector.operation.execute")
async def test_an_agent_cannot_call_an_ungranted_connector(world, provider, run):
    needs(MODEL_IS_REAL)
    alice = await world.person("priya")
    organization = alice.organization
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
    pod = await alice.creates_a_pod(named=run.name("connectors"))
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["CONNECTORS"])
    provider.clear()

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"List the widgets from the {auth_config['name']} connector and "
            f"tell me what you find."
        ),
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # The strongest possible statement, and the reason this one survives being
    # asked in words rather than scripted: nothing reached the provider at all.
    # Whether the model tried and was refused, or never tried, the promise is
    # the same and this assertion holds either way — where reading the agent's
    # reply would pass just as well if the call went out and the answer was
    # merely *worded* as a refusal.
    assert not provider.received, (
        "an agent with no connector grant reached the provider anyway: "
        f"{[call.path for call in provider.received]}"
    )


@scenario("Removing a person stops the agents working in their name")
@proves("PS-ACCESS-023")
@covers("pod.member.remove", "agent.conversation.get", "agent.conversation.message.send")
async def test_removing_a_person_stops_their_delegations(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("delegation"))
    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=pod, as_role="POD_EDITOR")

    # Bob delegates work to an agent, the way anyone would.
    agent = await bob.creates_an_agent(in_pod=pod, toolsets=["POD"])
    conversation = await bob.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="Have a look at the tables."
    )
    await bob.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    await alice.removes_member(await alice.membership_of(bob, in_pod=pod), from_pod=pod)

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
