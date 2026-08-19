"""Sharing and permissions → granting access to one specific resource."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [journey("Sharing and permissions"), capability("Grant access to one thing")]


@pytest.fixture
async def team(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))
    await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")
    return alice, bob, pod


@scenario("A person can see what they may do before trying it")
@proves("PS-ACCESS-012", "PS-POD-012")
@covers("pod.permissions.me", "pod.permissions.catalog")
async def test_effective_permissions_are_readable(team):
    alice, bob, pod = team

    catalog = await alice.permission_catalog_of(pod)
    mine = await alice.permissions_in(pod)
    theirs = await bob.permissions_in(pod)

    assert catalog, "the catalogue of what is grantable must not be empty"
    assert mine > theirs, "an admin must hold strictly more than a viewer"


@scenario("Reported permissions match what the API actually enforces")
@proves("PS-ACCESS-012", "PS-POD-012")
@covers("pod.permissions.me", "table.create")
async def test_reported_permissions_are_honest(team):
    alice, bob, pod = team

    assert "datastore.table.create" not in await bob.permissions_in(pod)
    await bob.is_refused_creating_a_table(in_pod=pod)

    assert "datastore.table.create" in await alice.permissions_in(pod)
    await alice.creates_a_table(in_pod=pod)


@scenario("A person can see who can reach a resource")
@proves("PS-ACCESS-030")
@covers("pod.resource_access.get")
async def test_resource_access_is_readable(team):
    alice, _bob, pod = team
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])

    access = await alice.access_to(
        resource_type="datastore_table", resource_name=table["name"], in_pod=pod
    )

    assert access is not None


@scenario("Nobody can grant permissions they do not hold themselves")
@proves("PS-ACCESS-010")
@covers("pod.resource_access.grant.replace")
async def test_nobody_confers_more_than_they_have(team):
    alice, bob, pod = team
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    bobs_membership = await alice.membership_of(bob, in_pod=pod)

    response = await bob.api.call(
        "PUT",
        f"/pods/{pod['id']}/resources/datastore_table/{table['name']}"
        f"/access/grantees/POD_MEMBER/{bobs_membership['pod_member_id']}",
        json={"permission_ids": ["datastore.table.delete"]},
    )

    assert response.status_code >= 400, (
        f"a viewer granted themselves table deletion ({response.status_code})"
    )
