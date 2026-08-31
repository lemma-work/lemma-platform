"""Getting started → organizations, ownership, and what an invitation carries.

These scenarios held the divergences from `issues.md` as `xfail(strict=True)`
markers until the code caught up; the markers are gone now that the promises
hold. The ids stay with the promises they prove.
"""

from __future__ import annotations


from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Getting started"), capability("Create an organization")]


@scenario("Two organizations may be called the same thing")
@proves("PS-ONB-014")
@covers("org.create")
async def test_two_organizations_may_share_a_display_name(world, run):
    alice = await world.new_person("alice")
    # One name, asked for once and used twice — which is the scenario. Built
    # through `run.name` because both organizations outlive the run and neither
    # can ever be deleted, so a name that says nothing about who made it is one
    # nobody can ever account for.
    shared = run.name("acme")
    await alice.creates_an_organization(named=shared)

    bob = await world.new_person("bob")
    # A different company that happens to have the same name. Handles are what
    # resolve; display names are labels.
    await bob.creates_an_organization(named=shared)


@scenario("An organization cannot be left with nobody able to administer it")
@proves("PS-ONB-041")
@covers("org.member.remove", "org.member.update_role")
async def test_the_last_owner_cannot_step_down(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    membership = await alice.own_membership_of(organization)

    demoted = await alice.api.call(
        "PATCH",
        f"/organizations/{organization['id']}/members/{membership['id']}/role",
        json={"role": "ORG_MEMBER"},
    )
    removed = await alice.api.call(
        "DELETE", f"/organizations/{organization['id']}/members/{membership['id']}"
    )

    assert demoted.status_code >= 400 and removed.status_code >= 400, (
        f"the only owner stepped down and the organization can never be "
        f"administered again: demote answered {demoted.status_code}, remove "
        f"answered {removed.status_code}"
    )


@scenario("An invitation naming a pod grants the pod as well as the organization")
@proves("PS-ONB-021")
@covers("org.invitation.invite", "org.invitation.accept", "pod.member.list")
async def test_an_invitation_carries_its_pod(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()

    bob = await world.new_person("bob")
    await bob.accepts(
        await alice.invites(bob, to=organization, pod=pod, pod_role="POD_USER")
    )

    joined = {str(m.get("user_id")) for m in await alice.members_of_pod(pod)}
    assert str(bob.user_id) in joined, (
        "bob accepted an invitation that named a pod and is not in it — which "
        "is usually the entire reason he accepted"
    )


@scenario("An invitation to a pod that has since gone says so rather than half-working")
@proves("PS-ONB-021")
@covers("org.invitation.accept", "pod.member.list")
async def test_an_invitation_to_a_vanished_pod_is_not_silently_half_applied(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()

    bob = await world.new_person("bob")
    invitation = await alice.invites(bob, to=organization, pod=pod, pod_role="POD_USER")
    # The pod goes away between the invitation being sent and accepted, which
    # is an ordinary amount of time for a pod to be reorganised.
    await alice.deletes_pod(pod)

    accepted = await bob.api.call(
        "POST", f"/organizations/invitations/{invitation['id']}/accept"
    )

    assert accepted.status_code >= 400, (
        f"accepting an invitation to a pod that no longer exists answered "
        f"{accepted.status_code} — bob is in the organization, is not in the "
        f"pod he was invited to, and the invitation cannot be replayed"
    )
