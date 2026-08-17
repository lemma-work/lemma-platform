"""Operating a deployment → what the usage record is, and what it is not.

Usage is what an operator bills against and argues from, so it has to be a
ledger: written once, attributed, and complete. The failure that matters is not
a wrong number — it is a *missing* one. A run that cost money and left no record
is invisible in every report, and nothing else in the product will notice.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.agent import answers, attempts
from harness.steps.datastore import column
from harness.waiting import eventually

pytestmark = [
    journey("Operating a deployment"),
    capability("Know what is being used"),
]


@pytest.fixture
async def after_a_run(world):
    """An organization whose agent has done something worth recording."""
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(in_pod=pod)
    conversation = await alice.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="Say something short."
    )
    await alice.waits_for_a_reply(in_conversation=conversation, in_pod=pod)
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)
    return alice, organization, pod, agent


async def _events(alice, organization) -> list[dict]:
    payload = await alice.api.get(
        f"/usage/organizations/{organization['id']}/events"
    )
    return list(payload.get("items") or payload.get("events") or [])


@scenario("Every model run leaves a record, priced or not")
@proves("PS-OPS-003", "PS-OPS-011")
@covers("usage.organization.events.list", "agent_run.completed")
async def test_a_run_is_always_recorded(after_a_run):
    alice, organization, _pod, _agent = after_a_run

    recorded = await eventually(
        lambda: _events(alice, organization),
        lambda events: bool(events),
        describe="the run to reach the usage ledger",
        timeout=60.0,
    )

    assert recorded, "a completed agent run left no usage record at all"

    # And where the price is unknown it says so, rather than recording zero.
    # A silent zero is the dangerous shape: it sums correctly, reports
    # correctly, and is wrong — with nothing anywhere to say the total is
    # missing part of its input. Asserted only where a price is actually
    # absent, so this holds whichever model a deployment happens to run.
    for entry in recorded:
        if entry.get("cost_usd") is None:
            assert (entry.get("metadata") or {}).get("pricing_missing"), (
                f"a run was recorded with no cost and no note that its price "
                f"was unknown, so it reads as free: {entry}"
            )


@scenario("A usage record says what spent, and on whose behalf")
@proves("PS-OPS-003")
@covers("usage.organization.events.list")
async def test_a_usage_record_is_attributed(after_a_run):
    alice, organization, _pod, _agent = after_a_run

    recorded = await eventually(
        lambda: _events(alice, organization),
        lambda events: bool(events),
        describe="the run to reach the usage ledger",
        timeout=60.0,
    )
    entry = recorded[0]

    # Without these a total cannot be broken down, and an operator asking "who
    # spent this" has only the total.
    for field in ("model_name", "organization_id", "source_type"):
        assert entry.get(field), (
            f"a usage record with no {field} cannot be attributed: {entry}"
        )
    assert entry.get("user_id") or entry.get("agent_id"), (
        f"nothing in this record says who or what spent it: {entry}"
    )
    assert entry.get("total_tokens"), (
        f"a record of a run that used no tokens is not a record of the run: {entry}"
    )


@scenario("An unpriceable model does not stop the work")
@proves("PS-OPS-011")
@covers("agent.conversation.message.send", "usage.organization.limits.get")
async def test_an_unknown_price_never_blocks_a_run(after_a_run):
    alice, organization, pod, agent = after_a_run

    # A second run, after the first has already been recorded unpriced. If an
    # incomplete price table could block work, this is where it would.
    conversation = await alice.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="And again, please."
    )
    messages = await alice.waits_for_a_reply(
        in_conversation=conversation, in_pod=pod
    )

    assert any(message.get("role") == "assistant" for message in messages), (
        "the second run produced no answer, so the platform refused work over "
        "its own pricing"
    )
    limits = await alice.api.get(
        f"/usage/organizations/{organization['id']}/limits"
    )
    assert limits is not None, "a deployment must be able to report its limits"


@scenario("A run that fails still costs, and is still recorded")
@proves("PS-OPS-003")
@covers("usage.organization.events.list", "agent.conversation.get")
async def test_a_failed_run_is_recorded_too(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD"])

    # A run that does real model work and then hits a refusal. The tokens were
    # spent whatever the outcome, so the ledger must show them.
    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "pod_write_record",
                action="delete",
                table_name=table["name"],
                record_id="00000000-0000-0000-0000-000000000001",
            ),
            answers("I could not do that."),
        ],
        saying="Delete that row.",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    recorded = await eventually(
        lambda: _events(alice, organization),
        lambda events: bool(events),
        describe="a run that did not get what it wanted to reach the ledger",
        timeout=60.0,
    )
    assert recorded, (
        "a run that spent tokens and then failed left no usage record, so its "
        "cost is invisible in every report"
    )
