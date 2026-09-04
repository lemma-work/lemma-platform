"""Sharing and permissions → granting to a role, and changing what a thing reaches.

Two things that look like sharing and are not quite. Granting to a *role* is
sharing with a job rather than a person, so it has to follow whoever holds that
job — including people who take it later. And changing a resource's reach is a
statement about people, which must not quietly take away what the pod's own
software depends on to work.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually, UNTIL_A_CHANGE_IS_VISIBLE

pytestmark = [
    journey("Sharing and permissions"),
    capability("Grant access to one thing"),
]


@pytest.fixture
async def a_pod_and_a_table(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("reach"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    try:
        yield alice, alice.organization, pod, table
    finally:
        await alice.deletes_pod(pod)


async def _can_read(person, table, pod) -> bool:
    """Whether this person may actually read the table's rows.

    A role's grant on one resource is not a pod-wide action, so it does not
    appear in `pod.permissions.me` — asking there reports "no" for a grant that
    works perfectly. The only honest question is whether the read succeeds.
    """
    response = await person.api.call(
        "GET", f"/pods/{pod['id']}/datastore/tables/{table['name']}/records"
    )
    return response.status_code < 400


async def _joins(world, alice, pod, who, *, as_role):
    """One of the cast joins this pod in a role.

    No invitation: they already work at Vantage Freight, which is the point of a
    standing cast. What a scenario is arranging here is who reaches this *pod*,
    and starting that from "sign a stranger up" was always scaffolding rather
    than something a person does.
    """
    person = await world.person(who)
    await alice.adds(person, to_pod=pod, as_role=as_role)
    return person


@scenario("A grant to a role reaches whoever holds that role, including later")
@proves("PS-ACCESS-011")
@covers(
    "pod.roles.create",
    "pod.role.permissions.replace",
    "pod.role.permissions.get",
    "pod.member.update_roles",
)
async def test_a_role_grant_follows_the_role(world, a_pod_and_a_table):
    alice, organization, pod, table = a_pod_and_a_table

    await alice.creates_a_role(in_pod=pod, named="auditor", permissions=[])
    await alice.replaces_role_permissions(
        "auditor",
        grants=[
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": ["datastore.table.read", "datastore.record.read"],
            }
        ],
        in_pod=pod,
    )

    # Bob holds the role from the start; carol is given it afterwards. Both must
    # end up with the same reach, or a grant to a role is really a grant to
    # whoever happened to be standing there when it was made.
    bob = await _joins(world, alice, pod, "sofia", as_role="auditor")
    carol = await _joins(world, alice, pod, "wei", as_role="POD_VIEWER")
    await alice.gives(carol, roles=["auditor"], in_pod=pod)

    for person in (bob, carol):
        reached = await eventually(
            lambda person=person: _can_read(person, table, pod),
            lambda allowed: allowed,
            describe=f"{person.label} to reach the table their role grants",
            timeout=UNTIL_A_CHANGE_IS_VISIBLE,
        )
        assert reached, person.label

    granted = await alice.permissions_of_role("auditor", in_pod=pod)
    assert granted, f"the role's grants read back empty: {granted}"


@scenario("Losing a role takes back what the role granted")
@proves("PS-ACCESS-011")
@covers("pod.member.update_roles", "pod.permissions.me")
async def test_losing_a_role_takes_back_its_grant(world, a_pod_and_a_table):
    alice, organization, pod, table = a_pod_and_a_table
    await alice.creates_a_role(in_pod=pod, named="auditor", permissions=[])
    await alice.replaces_role_permissions(
        "auditor",
        grants=[
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": ["datastore.table.read", "datastore.record.read"],
            }
        ],
        in_pod=pod,
    )
    bob = await _joins(world, alice, pod, "sofia", as_role="auditor")
    await eventually(
        lambda: _can_read(bob, table, pod),
        lambda allowed: allowed,
        describe="bob to reach the table his role grants",
        timeout=UNTIL_A_CHANGE_IS_VISIBLE,
    )

    # Moved to a role that grants nothing at all, rather than to POD_VIEWER —
    # a viewer can read tables in its own right, so the read continuing would
    # say nothing about whether the auditor grant was taken back.
    await alice.creates_a_role(in_pod=pod, named="bystander", permissions=[])
    await alice.gives(bob, roles=["bystander"], in_pod=pod)

    await eventually(
        lambda: _can_read(bob, table, pod),
        lambda allowed: not allowed,
        describe="bob to lose what the auditor role granted",
        timeout=UNTIL_A_CHANGE_IS_VISIBLE,
    )


@scenario("Changing who a resource is shared with leaves the pod's software working")
@proves("PS-ACCESS-003")
@covers(
    "pod.resource_access.grant.replace",
    "agent.permissions.get",
    "function.permissions.get",
)
async def test_narrowing_reach_keeps_workload_grants(world, a_pod_and_a_table):
    alice, organization, pod, table = a_pod_and_a_table
    bob = await _joins(world, alice, pod, "sofia", as_role="POD_EDITOR")

    agent = await alice.creates_an_agent(in_pod=pod)
    grants = [
        {
            "resource_type": "datastore_table",
            "resource_name": table["name"],
            "permission_ids": ["datastore.table.read", "datastore.record.read"],
        }
    ]
    await alice.replaces_agent_grants(agent["name"], grants=grants, in_pod=pod)
    before = await alice.grants_of_agent(agent["name"], in_pod=pod)

    # A sharing decision about a person.
    bobs_membership = await alice.membership_of(bob, in_pod=pod)
    await alice.api.delete(
        f"/pods/{pod['id']}/resources/datastore_table/{table['name']}"
        f"/access/grantees/POD_MEMBER/{bobs_membership['pod_member_id']}",
        what="alice narrowing who the table is shared with",
    )

    after = await alice.grants_of_agent(agent["name"], in_pod=pod)
    assert after == before, (
        "changing who a table is shared with also took away what the pod's own "
        f"agent was granted on it.\n  before: {before}\n  after:  {after}"
    )
