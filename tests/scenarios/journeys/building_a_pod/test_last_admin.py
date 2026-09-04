"""Building a pod → a pod always has somebody who can administer it."""

from __future__ import annotations


from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Building a pod"), capability("Change and remove membership")]


async def _pod_whose_only_admin_is_not_an_owner(world, run):
    """A pod administered by somebody who is not an organization owner.

    The plainest shape of the rule: nobody here has an organization role to
    fall back on, so a refusal can only be about the pod. The companion
    scenario below does the same thing with an owner, because the rule binds
    them too and for a while the code said otherwise.
    """
    owner = await world.person("priya")
    pod = await owner.creates_a_pod(named=run.name("lastadmin"))

    # Daniel is an ORG_EDITOR, not an owner, and that is the whole scenario.
    administrator = await world.person("daniel")
    await owner.adds(administrator, to_pod=pod, as_role="POD_ADMIN")

    # Somebody else is in the pod, so this is not about the pod being empty —
    # it is about it being left with nobody who can administer it.
    bystander = await world.person("sofia")
    await owner.adds(bystander, to_pod=pod, as_role="POD_VIEWER")

    # The owner steps out of the pod, leaving exactly one administrator who
    # cannot fall back on being an owner.
    await owner.removes_member(await owner.membership_of(owner, in_pod=pod), from_pod=pod)
    return administrator, pod


@scenario("A pod cannot be left with no admin")
@proves("PS-POD-041")
@covers("pod.member.update_roles")
async def test_the_last_pod_admin_cannot_step_down(world, run):
    administrator, pod = await _pod_whose_only_admin_is_not_an_owner(world, run)

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
async def test_the_last_pod_admin_cannot_remove_themselves(world, run):
    administrator, pod = await _pod_whose_only_admin_is_not_an_owner(world, run)

    refusal = await administrator.is_refused_removing(
        await administrator.membership_of(administrator, in_pod=pod), from_pod=pod
    )

    assert refusal == 409, (
        f"removing the pod's only admin answered {refusal}; leaving by the "
        f"other door has to meet the same rule as demotion"
    )


@scenario("An organization owner is not exempt from the pod's last-admin rule")
@proves("PS-POD-041")
@covers("pod.member.update_roles")
async def test_an_organization_owner_cannot_be_the_admin_who_steps_down(world, run):
    """The rule is about the pod, so it binds the owner of the organization too.

    Worth its own scenario because the tempting argument is that it should not:
    an owner reaches every pod in their organization, so the pod is never
    unreachable. But reachable is not administered. The pod's own member list
    is what every pod-scoped permission check reads, and a pod whose only
    administrator has just demoted themselves shows nobody who can manage it —
    a guarantee that holds only while a role held somewhere else is unchanged
    is not a guarantee.
    """
    owner = await world.person("priya")
    pod = await owner.creates_a_pod(named=run.name("ownerlastadmin"))

    # Somebody else is in the pod, so this is about it being left with nobody
    # who can administer it rather than about it being left empty.
    bystander = await world.person("sofia")
    await owner.adds(bystander, to_pod=pod, as_role="POD_VIEWER")

    refusal = await owner.is_refused_giving(owner, roles=["POD_VIEWER"], in_pod=pod)

    assert refusal == 409, (
        f"an organization owner demoted themselves out of being the pod's only "
        f"admin and got {refusal}; the pod rule binds them like anyone else"
    )
