"""Building a pod → a pod always has somebody who can administer it."""

from __future__ import annotations


from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Building a pod"), capability("Change and remove membership")]


async def _pod_whose_only_admin_is_not_an_owner(world):
    """A pod administered by somebody who is not an organization owner.

    The distinction is the whole scenario. An organization's owners are exempt
    from this rule on purpose — they reach every pod in their organization, so a
    pod with no admin is never stranded — which means a test that has the owner
    do the demoting exercises the *exemption* and passes while proving nothing
    about the rule it is named after.
    """
    owner = await world.new_person("alice")
    organization = await owner.creates_an_organization()
    pod = await owner.creates_a_pod()

    administrator = await world.new_person("bob")
    await administrator.accepts(await owner.invites(administrator, to=organization))
    await owner.adds(administrator, to_pod=pod, as_role="POD_ADMIN")

    # Somebody else is in the pod, so this is not about the pod being empty —
    # it is about it being left with nobody who can administer it.
    bystander = await world.new_person("carol")
    await bystander.accepts(await owner.invites(bystander, to=organization))
    await owner.adds(bystander, to_pod=pod, as_role="POD_VIEWER")

    # The owner steps out of the pod, leaving exactly one administrator who
    # cannot fall back on being an owner.
    await owner.removes_member(
        await owner.membership_of(owner, in_pod=pod), from_pod=pod
    )
    return administrator, pod


@scenario("A pod cannot be left with no admin")
@proves("PS-POD-041")
@covers("pod.member.update_roles")
async def test_the_last_pod_admin_cannot_step_down(world):
    administrator, pod = await _pod_whose_only_admin_is_not_an_owner(world)

    # They demote *themselves*. Anyone else doing it would be refused for
    # lacking the permission, and the scenario would pass while proving nothing.
    refusal = await administrator.is_refused_giving(
        administrator, roles=["POD_VIEWER"], in_pod=pod
    )

    assert refusal == 409, (
        f"demoting the pod's only admin answered {refusal}; the rule is a "
        f"conflict with the pod's current state, not a missing permission"
    )


@scenario("The last pod admin cannot remove themselves either")
@proves("PS-POD-041")
@covers("pod.member.remove")
async def test_the_last_pod_admin_cannot_remove_themselves(world):
    administrator, pod = await _pod_whose_only_admin_is_not_an_owner(world)

    refusal = await administrator.is_refused_removing(
        await administrator.membership_of(administrator, in_pod=pod), from_pod=pod
    )

    assert refusal == 409, (
        f"removing the pod's only admin answered {refusal}; leaving by the "
        f"other door has to meet the same rule as demotion"
    )
