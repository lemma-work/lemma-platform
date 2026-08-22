"""Building a pod → a pod always has somebody who can administer it."""

from __future__ import annotations


from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Building a pod"), capability("Change and remove membership")]


@scenario("A pod cannot be left with no admin")
@proves("PS-POD-041")
@covers("pod.member.remove", "pod.member.update_roles")
async def test_the_last_pod_admin_cannot_step_down(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()

    # There is somebody else in the pod, so this is not about the pod being
    # empty — it is about it being left with nobody who can administer it.
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))
    await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

    # Alice demotes *herself*. Anyone else doing it would be refused for
    # lacking the permission, and the scenario would pass while proving nothing
    # about the rule it is named after.
    membership = await alice.membership_of(alice, in_pod=pod)
    demoted = await alice.api.call(
        "PATCH",
        f"/pods/{pod['id']}/members/{membership['pod_member_id']}/roles",
        json={"roles": ["POD_VIEWER"]},
    )

    assert demoted.status_code >= 400, (
        f"the pod's only admin was demoted ({demoted.status_code}), leaving "
        f"nobody who can administer it"
    )
