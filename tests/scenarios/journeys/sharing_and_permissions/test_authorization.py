"""Sharing and permissions → who and what may touch each thing.

The rules that decide every request. A pod's boundary, a resource's reach, a
grant to one person, and the narrower rule that applies to software acting on
someone's behalf.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [
    journey("Sharing and permissions"),
    capability("Decide how widely a resource is shared"),
]


@pytest.fixture
async def team(world, run):
    """An admin, an ordinary member, and an outsider, around one pod.

    A pod of this run's own, because these scenarios change who may reach it and
    a standing pod that kept every change would be a different pod for the run
    after. The people are the standing cast, and that is what makes the outsider
    mean something: Hannah is refused because she genuinely works at Calder
    Retail, not because the harness made somebody who belongs nowhere.
    """
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("access"))
    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=pod, as_role="POD_USER")
    outsider = await world.person("hannah")
    try:
        yield alice, bob, outsider, pod
    finally:
        await alice.deletes_pod(pod)


@scenario("A resource created without saying is reachable by the pod")
@proves("PS-ACCESS-001")
@covers("table.create", "table.get", "record.list")
async def test_the_default_reach_is_the_pod(team):
    alice, bob, _outsider, pod = team

    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], shared=True
    )

    assert table["visibility"] == "POD", table
    await bob.opens_table(table["name"], in_pod=pod)


@scenario("A personal resource is not reachable by other pod members")
@proves("PS-ACCESS-001")
@covers("table.create", "table.get")
async def test_a_personal_resource_stays_personal(team):
    alice, bob, _outsider, pod = team

    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], visibility="PERSONAL", shared=True
    )

    assert table["visibility"] == "PERSONAL", table
    response = await bob.api.call(
        "GET", f"/pods/{pod['id']}/datastore/tables/{table['name']}"
    )
    assert response.status_code >= 400, (
        f"a personal resource was readable by another member ({response.status_code})"
    )


@scenario("An unauthenticated request is refused whatever the resource's reach")
@proves("PS-ACCESS-001")
@covers("table.get", "pod.get")
async def test_public_never_means_anonymous(world, team):
    alice, _bob, _outsider, pod = team
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], visibility="PUBLIC", shared=True
    )

    anonymous = await world.new_person("anonymous", sign_up=False)

    for path in (
        f"/pods/{pod['id']}",
        f"/pods/{pod['id']}/datastore/tables/{table['name']}",
    ):
        response = await anonymous.api.call("GET", path)
        assert response.status_code in (401, 403), (
            f"{path} answered {response.status_code} with no credentials; public "
            f"means every signed-in account, never anonymous"
        )


class TestGrantingOneThing:
    pytestmark = capability("Grant access to one specific thing")

    @scenario("A grant opens one resource without opening the rest")
    @proves("PS-ACCESS-010")
    @covers("pod.resource_access.grant.replace", "pod.resource_access.get", "table.get")
    async def test_a_grant_is_narrow(self, team):
        alice, bob, _outsider, pod = team
        granted = await alice.creates_a_table(
            in_pod=pod, named="granted_table", columns=[column("title")],
            visibility="RESTRICTED", shared=True,
        )
        withheld = await alice.creates_a_table(
            in_pod=pod, named="withheld_table", columns=[column("title")],
            visibility="RESTRICTED", shared=True,
        )
        membership = await alice.membership_of(bob, in_pod=pod)

        await alice.grants(
            ["datastore.table.read", "datastore.record.read"],
            on_type="datastore_table", on_name=granted["name"],
            to_member=membership, in_pod=pod,
        )

        await bob.opens_table(granted["name"], in_pod=pod)
        refused = await bob.api.call(
            "GET", f"/pods/{pod['id']}/datastore/tables/{withheld['name']}"
        )
        assert refused.status_code >= 400, (
            f"granting one table opened another ({refused.status_code})"
        )

    @scenario("Revoking a grant closes the resource again")
    @proves("PS-ACCESS-010", "PS-ACCESS-002")
    @covers("pod.resource_access.grant.delete", "table.get")
    async def test_revoking_closes_it_again(self, team):
        alice, bob, _outsider, pod = team
        table = await alice.creates_a_table(
            in_pod=pod, columns=[column("title")], visibility="RESTRICTED", shared=True
        )
        membership = await alice.membership_of(bob, in_pod=pod)
        await alice.grants(
            ["datastore.table.read"],
            on_type="datastore_table", on_name=table["name"],
            to_member=membership, in_pod=pod,
        )
        await bob.opens_table(table["name"], in_pod=pod)

        await alice.revokes_access(
            on_type="datastore_table", on_name=table["name"],
            from_member=membership, in_pod=pod,
        )

        refused = await bob.api.call(
            "GET", f"/pods/{pod['id']}/datastore/tables/{table['name']}"
        )
        assert refused.status_code >= 400, (
            f"a revoked grant still opened the table ({refused.status_code})"
        )

    @scenario("A grant reads back exactly what was given")
    @proves("PS-ACCESS-030")
    @covers("pod.resource_access.get", "pod.resource_access.grant.replace")
    async def test_a_grant_is_auditable(self, team):
        alice, bob, _outsider, pod = team
        table = await alice.creates_a_table(
            in_pod=pod, columns=[column("title")], visibility="RESTRICTED", shared=True
        )
        membership = await alice.membership_of(bob, in_pod=pod)
        await alice.grants(
            ["datastore.table.read"],
            on_type="datastore_table", on_name=table["name"],
            to_member=membership, in_pod=pod,
        )

        access = await alice.access_to(
            resource_type="datastore_table", resource_name=table["name"], in_pod=pod
        )

        assert "datastore.table.read" in str(access), access

    @scenario("A grant does not survive into another pod")
    @proves("PS-ACCESS-010")
    @covers("pod.resource_access.grant.replace", "table.get")
    async def test_a_grant_is_scoped_to_its_pod(self, team, run):
        alice, bob, _outsider, pod = team
        # Run-scoped, because a pod's name lives in the organization rather than
        # in the pod: the tables and roles below can keep their readable names
        # because the pod holding them goes at the end of the scenario, and this
        # cannot.
        elsewhere = await alice.creates_a_pod(named=run.name("elsewhere"))
        secret = await alice.creates_a_table(
            in_pod=elsewhere, columns=[column("title")], shared=True
        )

        refused = await bob.api.call(
            "GET", f"/pods/{elsewhere['id']}/datastore/tables/{secret['name']}"
        )

        assert refused.status_code >= 400, (
            f"a member of one pod read another pod's table ({refused.status_code})"
        )


class TestSoftwareActingForSomeone:
    pytestmark = capability("Give software exactly what it needs")

    @scenario("An agent is created with no standing access of its own")
    @proves("PS-ACCESS-020")
    @covers("agent.create", "agent.permissions.get")
    async def test_a_new_agent_holds_nothing(self, team):
        alice, _bob, _outsider, pod = team

        agent = await alice.creates_an_agent(in_pod=pod)

        grants = await alice.grants_of_agent(agent["name"], in_pod=pod)
        assert grants.get("grants") == [], (
            f"a new agent should start with no resource grants: {grants}"
        )

    @scenario("A function is created with no standing access of its own")
    @proves("PS-ACCESS-020")
    @covers("function.create", "function.permissions.get")
    @pytest.mark.sandbox
    async def test_a_new_function_holds_nothing(self, team):
        alice, _bob, _outsider, pod = team

        function = await alice.creates_a_function(in_pod=pod)

        grants = await alice.grants_of_function(function["name"], in_pod=pod)
        assert grants.get("grants") == [], (
            f"a new function should start with no resource grants: {grants}"
        )

    @scenario("Only someone who may administer an agent can change what it reaches")
    @proves("PS-ACCESS-020")
    @covers("agent.permissions.replace")
    async def test_a_member_cannot_widen_an_agent(self, team):
        alice, bob, _outsider, pod = team
        agent = await alice.creates_an_agent(in_pod=pod)
        table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])

        response = await bob.api.call(
            "PUT",
            f"/pods/{pod['id']}/agents/{agent['name']}/permissions",
            json={"grants": [{
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": ["datastore.table.delete"],
            }]},
        )

        assert response.status_code >= 400, (
            f"an ordinary member widened an agent's reach ({response.status_code})"
        )


class TestRefusals:
    pytestmark = capability("Understand and audit access")

    @scenario("A refusal names the permission that was missing")
    @proves("PS-ACCESS-031")
    @covers("table.create", "pod.permissions.me")
    async def test_a_refusal_is_informative(self, team):
        alice, bob, _outsider, pod = team

        response = await bob.api.call(
            "POST", f"/pods/{pod['id']}/datastore/tables",
            json={"name": "not_allowed", "columns": [column("title")]},
        )

        assert response.status_code == 403, response.status_code
        body = response.json()
        assert "permission" in str(body).lower(), (
            f"a refusal should say what was missing: {body}"
        )

    @scenario("A refusal does not reveal a pod in another organization")
    @proves("PS-ACCESS-031")
    @covers("pod.get", "table.get")
    async def test_a_refusal_does_not_leak(self, team):
        alice, _bob, outsider, pod = team
        table = await alice.creates_a_table(
            in_pod=pod, named="confidential_roadmap", columns=[column("title")]
        )

        response = await outsider.api.call(
            "GET", f"/pods/{pod['id']}/datastore/tables/{table['name']}"
        )

        assert response.status_code >= 400, response.status_code
        assert "confidential_roadmap" not in response.text, (
            f"the refusal echoed the resource name back: {response.text[:300]}"
        )
