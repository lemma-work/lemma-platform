"""Operating a deployment → what the usage record is, and what it is not.

Usage is what an operator bills against and argues from, so it has to be a
ledger: written once, attributed, and complete. The failure that matters is not
a wrong number — it is a *missing* one. A run that cost money and left no record
is invisible in every report, and nothing else in the product will notice.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.steps.datastore import column
from harness.waiting import eventually, UNTIL_A_RUN_SETTLES

pytestmark = [
    journey("Operating a deployment"),
    capability("Know what is being used"),
]


@pytest.fixture
async def after_a_run(world, run):
    """An organization whose agent has done something worth recording."""
    alice = await world.person("priya")
    organization = alice.organization
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    conversation = await alice.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"], saying="Say something short."
    )
    await alice.waits_for_a_reply(in_conversation=conversation, in_pod=pod)
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)
    return alice, organization, pod, agent


async def _events(alice, organization) -> list[dict]:
    payload = await alice.api.get(f"/usage/organizations/{organization['id']}/events")
    return list(payload.get("items") or payload.get("events") or [])


@scenario("Every model run leaves a record, priced or not")
@proves("PS-OPS-003", "PS-OPS-011")
@covers("usage.organization.events.list", "agent_run.completed")
async def test_a_run_is_always_recorded(after_a_run):
    alice, organization, _pod, _agent = after_a_run

    recorded = await eventually(
        lambda: _events(alice, organization),
        bool,
        describe="the run to reach the usage ledger",
        timeout=UNTIL_A_RUN_SETTLES,
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
        bool,
        describe="the run to reach the usage ledger",
        timeout=UNTIL_A_RUN_SETTLES,
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
    messages = await alice.waits_for_a_reply(in_conversation=conversation, in_pod=pod)

    assert any(message.get("role") == "assistant" for message in messages), (
        "the second run produced no answer, so the platform refused work over "
        "its own pricing"
    )
    limits = await alice.api.get(f"/usage/organizations/{organization['id']}/limits")
    assert limits is not None, "a deployment must be able to report its limits"


@scenario("A run that fails still costs, and is still recorded")
@proves("PS-OPS-003")
@covers("usage.organization.events.list", "agent.conversation.get")
async def test_a_failed_run_is_recorded_too(world, run):
    needs(MODEL_IS_REAL)
    alice = await world.person("priya")
    organization = alice.organization
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD"])

    # A run that does real model work and then does not get what it wanted. The
    # row named here has never existed, so whatever the agent tries, it will not
    # find it — and the tokens were spent looking either way, which is the whole
    # of this promise. Asked in words rather than by injecting the failing call:
    # what matters is that a run which ends unhappily still reaches the ledger,
    # and that is true however the agent arrives at being unable to help.
    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying=(
            f"Delete the row with id 00000000-0000-0000-0000-000000000001 from "
            f"the {table['name']} table."
        ),
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    recorded = await eventually(
        lambda: _events(alice, organization),
        bool,
        describe="a run that did not get what it wanted to reach the ledger",
        timeout=UNTIL_A_RUN_SETTLES,
    )
    assert recorded, (
        "a run that spent tokens and then failed left no usage record, so its "
        "cost is invisible in every report"
    )


@scenario("An operator can inspect a single run’s usage")
@proves("PS-OPS-003")
@covers("usage.organization.events.list")
async def test_usage_can_be_filtered_to_a_run(after_a_run):
    alice, organization, _pod, _agent = after_a_run
    recorded = await eventually(
        lambda: _events(alice, organization),
        bool,
        describe="the run to reach the usage ledger",
        timeout=UNTIL_A_RUN_SETTLES,
    )
    run_id = next(
        entry["agent_run_id"] for entry in recorded if entry.get("agent_run_id")
    )
    payload = await alice.api.get(
        f"/usage/organizations/{organization['id']}/events?agent_run_id={run_id}"
    )
    assert payload["items"]
    assert all(entry["agent_run_id"] == run_id for entry in payload["items"])
    assert all(
        entry["cost_source"] in {"REGISTERED", "ESTIMATED", "UNKNOWN", "LEGACY"}
        for entry in payload["items"]
    )
