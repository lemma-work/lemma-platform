"""Operating a deployment → knowing what is being used."""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Operating a deployment"), capability("Know what is being used")]


@scenario("An organization can see its usage")
@proves("PS-OPS-001")
@covers("usage.organization.summary.get", "usage.organization.events.list")
async def test_usage_is_readable_by_a_member(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    summary = await alice.usage_summary_of(organization)

    assert summary is not None


@scenario("A person can see their own usage without administrative access")
@proves("PS-OPS-002")
@covers("usage.organization.me.summary.get")
async def test_own_usage_is_readable(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    mine = await alice.own_usage_in(organization)

    assert mine is not None


@scenario("Someone outside an organization cannot read its usage")
@proves("PS-OPS-001")
@covers("usage.organization.summary.get")
async def test_an_outsider_cannot_read_usage(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    outsider = await world.new_person("outsider")

    await outsider.is_refused_usage_of(organization)


@scenario("Limits are visible before they are hit")
@proves("PS-OPS-010")
@covers("usage.organization.limits.get")
async def test_limits_are_visible(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    limits = await alice.api.get(f"/usage/organizations/{organization['id']}/limits")

    assert limits is not None
