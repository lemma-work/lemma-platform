"""Building a pod → custom roles, and deleting the pod."""

from __future__ import annotations

import pytest


from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Building a pod"),
    capability("Define roles the built-ins do not cover"),
]


@pytest.fixture
async def pod(world, run):
    alice = await world.person("priya")
    return alice, await alice.creates_a_pod(named=run.name("pod"))


@scenario("A pod admin defines a custom role and it can be assigned")
@proves("PS-POD-013")
@covers("pod.roles.create", "pod.roles.list", "pod.permissions.catalog")
async def test_a_custom_role_is_created_and_assignable(world, pod):
    alice, the_pod = pod
    catalog = await alice.permission_catalog_of(the_pod)
    readable = [p["id"] for p in catalog if str(p.get("id", "")).endswith(".read")][:3]
    assert readable, catalog

    role = await alice.creates_a_role(
        in_pod=the_pod, named="REPORT_READER", permissions=readable
    )

    assert role["name"] == "REPORT_READER"
    assert "REPORT_READER" in {r["name"] for r in await alice.roles_in(the_pod)}

    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=the_pod, as_role="REPORT_READER")

    membership = await alice.membership_of(bob, in_pod=the_pod)
    assert membership["roles"] == ["REPORT_READER"], membership


@scenario("Nobody can create a role carrying permissions they do not hold")
@proves("PS-POD-013")
@covers("pod.roles.create")
async def test_a_role_cannot_exceed_its_creator(world, pod):
    alice, the_pod = pod
    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=the_pod, as_role="POD_VIEWER")

    response = await bob.api.call(
        "POST",
        f"/pods/{the_pod['id']}/roles",
        json={"name": "SNEAKY_ADMIN", "permission_ids": ["datastore.table.delete"]},
    )

    assert response.status_code >= 400, (
        f"a viewer minted a role granting table deletion ({response.status_code})"
    )


class TestDeletingAPod:
    pytestmark = capability("Delete a pod")

    @scenario("A pod admin deletes a pod and it stops being listed")
    @proves("PS-POD-050")
    @covers("pod.delete", "pod.list", "pod.deleted")
    async def test_deleting_a_pod_removes_it(self, pod):
        alice, the_pod = pod

        await alice.deletes_pod(the_pod)

        await alice.does_not_see_pod(the_pod)

    @scenario("Deleting a pod frees its name for reuse")
    @proves("PS-POD-002", "PS-POD-050")
    @covers("pod.delete", "pod.create")
    async def test_a_deleted_pods_name_is_reusable(self, world, pod, run):
        alice, _the_pod = pod
        reusable = run.name("reusable")
        named = await alice.creates_a_pod(named=reusable)

        await alice.deletes_pod(named)

        again = await alice.creates_a_pod(named=reusable)
        assert again["name"] == reusable
        assert str(again["id"]) != str(named["id"])

    @scenario("Deleting a pod twice reports success both times")
    @proves("PS-POD-050")
    @covers("pod.delete")
    async def test_a_repeated_deletion_still_reports_success(self, pod):
        """A retry is what a client does when it never saw the first answer.

        Worth pinning because the pod stops answering for everything else the
        moment it is deleted — its schedules, its agents, its records all 404
        — and deletion is the one operation that has to keep working through
        that, or a dropped response turns into an error a person cannot clear.
        """
        alice, the_pod = pod

        await alice.deletes_pod(the_pod)
        await alice.deletes_pod(the_pod)

        await alice.does_not_see_pod(the_pod)

    @scenario("A pod member who is not an admin cannot delete the pod")
    @proves("PS-POD-050")
    @covers("pod.delete")
    async def test_a_non_admin_cannot_delete_the_pod(self, world, pod):
        alice, the_pod = pod
        bob = await world.person("sofia")
        await alice.adds(bob, to_pod=the_pod, as_role="POD_EDITOR")

        await bob.is_refused_deleting_pod(the_pod)

        await alice.opens_pod(the_pod)

    @scenario("Deleting one pod leaves the organization's other pods alone")
    @proves("PS-POD-051")
    @covers("pod.delete", "pod.get", "pod.list")
    async def test_deleting_one_pod_spares_the_others(self, pod, run):
        alice, the_pod = pod
        survivor = await alice.creates_a_pod(named=run.name("survivor"))

        await alice.deletes_pod(the_pod)

        await alice.opens_pod(survivor)
        await alice.sees_pod(survivor)


@scenario("A role naming a permission that does not exist is refused, not crashed")
@proves("PS-POD-013")
@covers("pod.roles.create")
async def test_an_unknown_permission_is_refused_clearly(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("pod"))

    response = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/roles",
        json={
            "name": "custodian",
            # Plausible, and wrong: the real one is `pod.member.manage`. Getting
            # this wrong is what a person building a role actually does.
            "permission_ids": ["pod.member.remove"],
        },
    )

    assert 400 <= response.status_code < 500, (
        f"an unknown permission id answered {response.status_code} rather than "
        f"telling the caller what was wrong: {response.text[:400]}"
    )
