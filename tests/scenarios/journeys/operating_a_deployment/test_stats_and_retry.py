"""Operating a deployment → usage detail, and retrying failed work."""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [
    journey("Operating a deployment"),
    capability("Know what is being used"),
]


@scenario("Usage can be broken down rather than only totalled")
@proves("PS-OPS-001")
@covers("usage.organization.stats.get", "usage.organization.events.list")
async def test_usage_can_be_broken_down(world):
    alice = await world.person("priya")
    organization = alice.organization

    stats = await alice.usage_stats_of(organization)
    events = await alice.api.get(
        f"/usage/organizations/{organization['id']}/events"
    )

    assert stats is not None, stats
    assert events is not None, events


@scenario("Retrying a firing that does not exist is refused")
@proves("PS-SCHED-022")
@covers("schedule.run.retry")
async def test_retrying_an_unknown_firing_is_refused(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], shared=True
    )
    schedule = await alice.creates_a_schedule(
        in_pod=pod, kind="DATASTORE",
        config={"table_name": table["name"], "operations": ["INSERT"]},
        agent=agent["name"],
    )

    response = await alice.retries_firing(
        {"id": "00000000-0000-0000-0000-000000000001"},
        schedule=schedule, in_pod=pod,
    )

    assert response.status_code >= 400, (
        f"a firing that never happened must not be retryable "
        f"({response.status_code})"
    )
