"""Building a pod → finding members, and shaping what roles may do."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Building a pod"), capability("Put people in a pod")]


@pytest.fixture
async def team(world, run):
    alice = await world.person("priya")
    organization = alice.organization
    pod = await alice.creates_a_pod(named=run.name("pod"))
    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=pod, as_role="POD_USER")
    return alice, bob, organization, pod


@scenario("A person finds a pod member by email or by who they are")
@proves("PS-POD-010")
@covers("pod.member.get", "pod.member.lookup_by_email", "pod.member.lookup_by_user_id")
async def test_a_member_can_be_found(team):
    alice, bob, _organization, pod = team
    membership = await alice.membership_of(bob, in_pod=pod)

    by_id = await alice.opens_membership(membership, in_pod=pod)
    by_email = await alice.finds_member_by_email(bob.email, in_pod=pod)
    by_user = await alice.finds_member_by_user(str(bob.user_id), in_pod=pod)

    for found in (by_id, by_email, by_user):
        assert str(found.get("user_id")) == str(bob.user_id), found


@scenario("A person can see what they asked for, and whether it was decided")
@proves("PS-POD-021")
@covers("pod.join_request.me", "pod.join_request.create")
async def test_a_person_sees_their_own_request(world, team):
    alice, _bob, organization, pod = team
    carol = await world.person("wei")

    assert await carol.my_join_request_for(pod) in (None, {}, []), (
        "someone who has asked for nothing should have nothing waiting"
    )
    await carol.requests_to_join(pod)

    mine = await carol.my_join_request_for(pod)
    assert mine and str(mine.get("status")).upper() == "PENDING", mine


@scenario("A person previews what a kind of resource holds before opening it")
@proves("PS-ACCESS-030")
@covers("pod.resource.preview")
async def test_a_resource_can_be_previewed(team):
    alice, _bob, _organization, pod = team
    agent = await alice.creates_an_agent(in_pod=pod)

    preview = await alice.previews("agent", named=agent["name"], in_pod=pod)

    assert preview is not None, preview


class TestShapingRoles:
    pytestmark = capability("Define roles the built-ins do not cover")

    @scenario("A custom role's permissions can be read and replaced")
    @proves("PS-POD-013")
    @covers(
        "pod.role.permissions.get", "pod.role.permissions.replace", "pod.roles.create"
    )
    async def test_a_roles_permissions_can_change(self, team):
        alice, _bob, _organization, pod = team
        catalog = [
            p["id"]
            for p in await alice.permission_catalog_of(pod)
            if str(p.get("id", "")).endswith(".read")
        ]
        role = await alice.creates_a_role(
            in_pod=pod, named="READER", permissions=catalog[:2]
        )

        table = await alice.creates_a_table(in_pod=pod)
        await alice.replaces_role_permissions(
            role["name"],
            grants=[
                {
                    "resource_type": "datastore_table",
                    "resource_name": table["name"],
                    "permission_ids": ["datastore.table.read"],
                }
            ],
            in_pod=pod,
        )

        held = await alice.permissions_of_role(role["name"], in_pod=pod)
        assert table["name"] in str(held), (
            f"a role's resource grants should read back: {held}"
        )

    @scenario("A custom role can be described and removed")
    @proves("PS-POD-013")
    @covers("pod.roles.update", "pod.roles.delete", "pod.roles.list")
    async def test_a_role_can_be_described_and_removed(self, team):
        alice, _bob, _organization, pod = team
        catalog = [
            p["id"]
            for p in await alice.permission_catalog_of(pod)
            if str(p.get("id", "")).endswith(".read")
        ]
        role = await alice.creates_a_role(
            in_pod=pod, named="TEMPORARY", permissions=catalog[:1]
        )

        described = await alice.changes_role_definition(
            role["name"], in_pod=pod, description="Read-only, for auditors"
        )
        assert described.get("description") == "Read-only, for auditors", described

        await alice.deletes_role(role["name"], in_pod=pod)
        assert role["name"] not in {r["name"] for r in await alice.roles_in(pod)}
