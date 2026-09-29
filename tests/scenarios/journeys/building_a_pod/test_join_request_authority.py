"""Building a pod → approving a join request is not a way to gain authority.

Approving is the one place where deciding about *someone else's* access hands
the decider a role to choose. If that choice is unbounded, then pod admin — a
role an organization member can hold — becomes a route to organization owner,
and the org/pod boundary stops meaning anything.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, open_signup, proves, scenario
from harness.credentials import needs
from harness.environment import OPEN_SIGNUP

pytestmark = [
    journey("Building a pod"),
    capability("Let people ask to join"),
]


@pytest.fixture
async def pod_admin_who_is_only_a_member(world, run):
    """Bob runs a pod but is an ordinary member of the organization.

    That combination is the whole point: everything he can confer has to be
    capped by what he holds himself, and what he holds in the organization is
    the lowest role there is.
    """
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("pod"))

    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=pod, as_role="POD_ADMIN")

    # Somebody in no organization at all. That is what makes the scenario mean
    # something: approving their request is what would confer an organization
    # role, and a colleague who already has one has nothing to be conferred.
    needs(OPEN_SIGNUP)
    carol = await world.new_person("carol")
    await alice.opens_pod_to(pod, who="ORG_MEMBERS")
    request = await carol.requests_to_join(pod)
    return alice, bob, carol, alice.organization, pod, request


@scenario("A pod admin cannot mint an organization owner by approving a request")
@proves("PS-POD-022")
@covers("pod.join_request.approve", "org.member.list")
@open_signup
async def test_approving_cannot_confer_a_higher_organization_role(
    pod_admin_who_is_only_a_member,
):
    alice, bob, carol, organization, pod, request = pod_admin_who_is_only_a_member

    await bob.is_refused_approving(request, for_pod=pod, org_role="ORG_OWNER")

    # And it was refused rather than partly applied: Carol is not an owner, and
    # is not quietly in the organization on some lesser footing either.
    owners = [
        member
        for member in await alice.members_of(organization)
        if str(member.get("role")) == "ORG_OWNER"
    ]
    assert [str(o["user_id"]) for o in owners] == [str(alice.user_id)], (
        f"the organization gained an owner it should not have: {owners}"
    )


@scenario("A pod admin can still approve at the level they actually hold")
@proves("PS-POD-022")
@covers("pod.join_request.approve", "pod.member.list")
@open_signup
async def test_approving_within_your_own_authority_is_allowed(
    pod_admin_who_is_only_a_member,
):
    alice, bob, carol, organization, pod, request = pod_admin_who_is_only_a_member
    del alice, organization

    await bob.approves(request, for_pod=pod, as_role="POD_USER", org_role="ORG_MEMBER")

    joined = {str(m.get("user_id")) for m in await bob.members_of_pod(pod)}
    assert str(carol.user_id) in joined, (
        "capping what an approver may confer must not stop them approving at "
        "all — the refusal above has to be about the level, not the action"
    )


@scenario("An approver cannot confer pod permissions they do not hold")
@proves("PS-POD-022", "PS-ACCESS-010")
@covers("pod.join_request.approve", "pod.roles.create")
async def test_approving_cannot_confer_unheld_pod_permissions(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    await alice.opens_pod_to(pod, who="ORG_MEMBERS")

    # A role strictly above what an editor holds, so conferring it would be an
    # escalation rather than a sideways move. Managing members is an admin's
    # job, which is exactly what makes it unavailable to the editor below.
    await alice.creates_a_role(
        in_pod=pod,
        named="custodian",
        permissions=["datastore.table.delete", "pod.member.manage"],
    )

    bob = await world.person("sofia")
    await alice.adds(bob, to_pod=pod, as_role="POD_EDITOR")

    carol = await world.person("wei")
    request = await carol.requests_to_join(pod)

    refused = await bob.api.call(
        "POST",
        f"/pods/{pod['id']}/join-requests/{request['id']}/approve",
        json={"pod_role": "custodian", "org_role": "ORG_MEMBER"},
    )

    assert refused.status_code >= 400, (
        f"an editor conferred a role carrying permissions they do not hold "
        f"({refused.status_code})"
    )


@scenario("An organization editor who does not run a pod cannot mint its administrator")
@proves("PS-POD-021", "PS-POD-022")
@covers(
    "pod.join_request.create",
    "pod.join_request.approve",
    "pod.join_request.list",
    "pod.member.list",
)
async def test_an_organization_editor_cannot_make_themselves_a_pod_administrator(
    world, run
):
    """Managing the organization's people is not authority inside a pod.

    Daniel is an organization editor and belongs to none of this pod. Approving
    requests answers to the same authority as adding a member, so he cannot
    decide anyone's -- his own included. Without that he could ask to join any
    pod in the organization and approve himself as its administrator.
    """
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    daniel = await world.person("daniel")

    request = await daniel.requests_to_join(pod)

    await daniel.is_refused_approving(
        request, for_pod=pod, org_role="ORG_MEMBER", pod_role="POD_ADMIN"
    )
    await daniel.is_refused_pod(pod)
    members = {str(m.get("user_id")) for m in await alice.members_of_pod(pod)}
    assert str(daniel.user_id) not in members, (
        "an organization editor became a member of a pod they do not run by "
        "approving their own request"
    )


@scenario("An invitation cannot carry a pod role its author could not confer")
@proves("PS-ONB-021", "PS-POD-022")
@covers("org.invitation.invite", "pod.member.list")
@open_signup
async def test_an_invitation_cannot_smuggle_a_pod_administrator(world, run):
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    daniel = await world.person("daniel")
    ghost = await world.new_person("ghost")

    await daniel.is_refused_inviting(
        ghost, to=alice.organization, pod=pod, pod_role="POD_ADMIN"
    )
    await alice.invites(ghost, to=alice.organization, pod=pod, pod_role="POD_USER")


@scenario("An organization owner adds people to a pod they do not belong to")
@proves("PS-POD-010")
@covers("pod.member.add", "pod.member.update_roles", "pod.member.remove")
async def test_an_organization_owner_administers_a_pod_they_are_not_in(world, run):
    """The owner reaches every pod in the organization; adding is part of reach."""
    priya = await world.person("priya")
    pod = await priya.creates_a_pod(named=run.name("pod"))
    # Somebody other than the creator, holding no role in the pod.
    daniel = await world.person("daniel")
    await priya.adds(daniel, to_pod=pod, as_role="POD_ADMIN")
    await priya.removes_member(await priya.membership_of(priya, in_pod=pod), from_pod=pod)

    sofia = await world.person("sofia")
    await priya.adds(sofia, to_pod=pod, as_role="POD_VIEWER")

    members = {str(m.get("user_id")) for m in await priya.members_of_pod(pod)}
    assert str(sofia.user_id) in members
