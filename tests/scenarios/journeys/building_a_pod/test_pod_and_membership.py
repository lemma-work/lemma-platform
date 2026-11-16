"""Building a pod → creating one, and deciding who is in it.

Proves promises in
[docs/product/journeys/building-a-pod.md](../../../../docs/product/journeys/building-a-pod.md).
"""

from __future__ import annotations

from harness import capability, covers, journey, open_signup, proves, scenario
from harness.credentials import needs
from harness.environment import OPEN_SIGNUP

pytestmark = [journey("Building a pod"), capability("Create a pod")]


@scenario("A member of an organization creates a pod and administers it")
@proves("PS-POD-001")
@covers("pod.create", "pod.member.list", "pod.created")
async def test_pod_creator_administers_it(world, run):
    alice = await world.person("priya")

    pod = await alice.creates_a_pod(named=run.name("pod"))

    members = await alice.members_of_pod(pod)
    mine = next(m for m in members if str(m["user_id"]) == str(alice.user_id))
    assert "POD_ADMIN" in mine["roles"], mine


@scenario("Someone outside the organization cannot create a pod in it")
@proves("PS-POD-001")
@covers("pod.create")
@open_signup
async def test_outsider_cannot_create_a_pod(world, run):
    alice = await world.person("priya")
    organization = alice.organization
    # Somebody who belongs to no organization at all, which is what this
    # promise is written about. Kept as a fresh person rather than moved to the
    # standing cast, because every one of the cast belongs somewhere.
    needs(OPEN_SIGNUP)
    outsider = await world.new_person("outsider")

    response = await outsider.api.call(
        "POST",
        "/pods",
        json={
            "organization_id": str(organization["id"]),
            # Run-scoped, and that matters more than it looks: with a fixed name
            # the second run against a tenant is refused for the name already
            # being taken, and 409 satisfies "was it refused?" just as well as
            # the 403 this scenario is actually about. It would have gone green
            # having stopped testing authorization at all.
            "name": run.name("trespass"),
            "type": "HYBRID",
        },
    )

    assert response.status_code >= 400, (
        "organization membership is the outer boundary; a non-member creating a "
        f"pod inside it got {response.status_code}"
    )


@scenario("A pod's name identifies it within its organization")
@proves("PS-POD-002")
@covers("pod.create", "pod.list")
async def test_pod_names_are_unique_within_an_organization(world, run):
    alice = await world.person("priya")
    taken = run.name("support")
    await alice.creates_a_pod(named=taken)

    response = await alice.api.call(
        "POST",
        "/pods",
        json={
            "organization_id": str(alice.organization["id"]),
            "name": taken,
            "type": "HYBRID",
        },
    )

    assert response.status_code == 409, (
        "a name has to resolve to one pod — `lemma --pod <name>`, bundle "
        f"references and inbound mail all go through it. Got {response.status_code}"
    )


@scenario("The same pod name is free in a different organization")
@proves("PS-POD-002")
@covers("pod.create")
async def test_a_pod_name_is_scoped_to_its_organization(world, run):
    # Two real companies rather than two organizations made for the occasion:
    # Priya's Vantage Freight and Hannah's Calder Retail. An organization cannot
    # be deleted, so a scenario that made one to prove a point would leave it on
    # the deployment for good.
    priya = await world.person("priya")
    hannah = await world.person("hannah")
    shared_name = run.name("support")
    await priya.creates_a_pod(named=shared_name)

    elsewhere = await hannah.creates_a_pod(named=shared_name)

    assert elsewhere["name"] == shared_name
    assert str(elsewhere["organization_id"]) == str(hannah.organization["id"]), (
        "the same name in another organization has to be a different pod"
    )


class TestPuttingPeopleInAPod:
    pytestmark = capability("Put people in a pod")

    @scenario("A pod admin adds an organization member to the pod")
    @proves("PS-POD-010")
    @covers("pod.member.add", "pod.member.list", "pod.member_joined")
    async def test_admin_adds_an_organization_member(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))

        bob = await world.person("sofia")

        await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

        membership = await alice.membership_of(bob, in_pod=pod)
        assert membership["roles"] == ["POD_VIEWER"], membership

    @scenario("A pod viewer can read the pod but cannot write to it")
    @proves("PS-POD-011")
    @covers("pod.member.add", "pod.permissions.me", "pod.get")
    async def test_a_viewer_reads_but_does_not_write(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))

        bob = await world.person("sofia")
        await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

        await bob.can_read(pod)
        await bob.cannot_write_to(pod)

    @scenario("Someone with no pod membership cannot open the pod")
    @proves("PS-POD-030")
    @covers("pod.get", "pod.list")
    async def test_a_non_member_cannot_open_the_pod(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))

        bob = await world.person("sofia")

        # In the organization, but not in this pod.
        await bob.is_refused_pod(pod)
        await bob.does_not_see_pod(pod)

    @scenario("A person outside the organization cannot open the pod")
    @proves("PS-POD-030")
    @covers("pod.get")
    async def test_an_outsider_cannot_open_the_pod(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        outsider = await world.person("hannah")

        await outsider.is_refused_pod(pod)

    @scenario("Changing someone's role changes what they may do, immediately")
    @proves("PS-POD-011")
    @covers("pod.member.update_roles", "pod.permissions.me")
    async def test_a_role_change_applies_to_the_next_request(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        bob = await world.person("sofia")
        await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

        as_viewer = await bob.permissions_in(pod)
        assert "datastore.table.create" not in as_viewer, as_viewer

        await alice.gives(bob, roles=["POD_EDITOR"], in_pod=pod)

        # One read, deliberately not polled. "Immediately" in the promise means
        # the request after the one that changed the role, so a poll here would
        # quietly weaken the scenario into "eventually". It holds because the
        # snapshot invalidation runs inside the commit and the commit finishes
        # before the response — see the note on ``UoWDep``.
        as_editor = await bob.permissions_in(pod)
        assert as_editor > as_viewer, (as_viewer, as_editor)
        assert "datastore.table.create" in as_editor, as_editor

    @scenario("Removing someone from a pod takes their access away immediately")
    @proves("PS-POD-040")
    @covers("pod.member.remove", "pod.get")
    async def test_removing_a_member_revokes_access(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        bob = await world.person("sofia")
        await alice.adds(bob, to_pod=pod, as_role="POD_EDITOR")
        await bob.can_read(pod)

        membership = await alice.membership_of(bob, in_pod=pod)
        await alice.removes_member(membership, from_pod=pod)

        await bob.is_refused_pod(pod)

    @scenario("A pod member who is not an admin cannot remove other members")
    @proves("PS-POD-040")
    @covers("pod.member.remove")
    async def test_a_non_admin_cannot_remove_members(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        bob = await world.person("sofia")
        carol = await world.person("wei")
        for person in (bob, carol):
            await alice.adds(person, to_pod=pod, as_role="POD_EDITOR")

        carols_membership = await alice.membership_of(carol, in_pod=pod)
        await bob.is_refused_removing(carols_membership, from_pod=pod)

        # Carol is still there.
        await alice.membership_of(carol, in_pod=pod)
        await carol.can_read(pod)


class TestLettingPeopleAskToJoin:
    pytestmark = capability("Let people ask to join")

    @scenario("A pod defaults to invite-only")
    @proves("PS-POD-020")
    @covers("pod.create", "pod.join")
    async def test_a_new_pod_is_invite_only(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        bob = await world.person("sofia")

        await bob.is_refused_joining(pod)

    @scenario("A pod open to the organization lets any member walk in")
    @proves("PS-POD-020")
    @covers("pod.update", "pod.join", "pod.member_joined")
    async def test_an_org_open_pod_admits_members(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        await alice.opens_pod_to(pod, who="ORG_MEMBERS")

        bob = await world.person("sofia")
        await bob.joins(pod)

        await bob.can_read(pod)
        membership = await alice.membership_of(bob, in_pod=pod)
        assert membership["roles"] == ["POD_USER"], (
            "self-joining grants the base role, not a chosen one"
        )

    @scenario("Someone outside the organization cannot walk into an org-open pod")
    @proves("PS-POD-020")
    @covers("pod.update", "pod.join")
    @open_signup
    async def test_an_outsider_cannot_join_an_org_open_pod(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        await alice.opens_pod_to(pod, who="ORG_MEMBERS")
        # As above: "outsider" here means somebody in no organization.
        needs(OPEN_SIGNUP)
        outsider = await world.new_person("outsider")

        await outsider.is_refused_joining(pod)

    @scenario("A person asks for access and an admin grants it")
    @proves("PS-POD-021")
    @covers(
        "pod.join_request.create", "pod.join_request.list", "pod.join_request.approve"
    )
    async def test_a_join_request_is_approved(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("pod"))
        bob = await world.person("sofia")

        await bob.requests_to_join(pod)
        pending = await alice.join_requests_for(pod)
        assert len(pending) == 1, pending

        await alice.approves(pending[0], for_pod=pod, as_role="POD_USER")

        await bob.can_read(pod)

    @scenario("A member finds the pods open to them, and joins one")
    @proves("PS-POD-023")
    @covers("pod.create", "pod.update", "pod.list_joinable", "pod.join", "pod.list")
    async def test_a_member_finds_the_pods_open_to_them(self, world, run):
        alice = await world.person("priya")
        open_pod = await alice.creates_a_pod(named=run.name("open"))
        await alice.opens_pod_to(open_pod, who="ORG_MEMBERS")
        closed_pod = await alice.creates_a_pod(named=run.name("closed"))
        bob = await world.person("sofia")

        offered = {str(pod["id"]): pod for pod in await bob.pods_they_can_join()}

        assert str(closed_pod["id"]) not in offered, "an invite-only pod is offered"
        found = offered.get(str(open_pod["id"]))
        assert found is not None, "the pod open to the organization is not offered"
        assert found["member_count"] == 1, (
            f"offered with {found['member_count']} members; its one member is "
            f"the admin who made it: {found}"
        )

        await bob.joins(open_pod)

        await bob.sees_pod(open_pod)
        still_offered = {str(pod["id"]) for pod in await bob.pods_they_can_join()}
        assert str(open_pod["id"]) not in still_offered, (
            "a pod they are in is still offered to them"
        )

    @scenario("An owner is offered no pod to join, and an outsider is refused")
    @proves("PS-POD-023")
    @covers("pod.create", "pod.update", "pod.list_joinable", "pod.delete")
    async def test_an_owner_is_offered_nothing_and_an_outsider_is_refused(
        self, world, run
    ):
        alice = await world.person("priya")
        open_pod = await alice.creates_a_pod(named=run.name("open"))
        await alice.opens_pod_to(open_pod, who="ORG_MEMBERS")
        outsider = await world.person("hannah")

        # An owner already opens every pod, and joining one is refused to them.
        assert await alice.pods_they_can_join() == []
        refusal = await outsider.is_refused_the_pods_open_in(alice.organization)
        assert refusal == 403, f"someone from another organization was answered {refusal}"

        # Nobody joins it, so it would be offered to the cast on every run after.
        await alice.deletes_pod(open_pod)

    @scenario("A pod cannot take in new members while it is invite-only")
    @proves("PS-POD-024")
    @covers("pod.create", "pod.update")
    async def test_an_invite_only_pod_cannot_take_in_new_members(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("closed"))

        refusal = await alice.is_refused_making_new_members_join(pod)

        assert refusal == "POD_AUTO_JOIN_NEEDS_OPEN_POLICY", refusal

    @scenario("Only someone who decides who joins a pod may make new members join it")
    @proves("PS-POD-024")
    @covers("pod.create", "pod.update", "pod.member.add")
    async def test_a_pod_editor_cannot_make_new_members_join(self, world, run):
        alice = await world.person("priya")
        pod = await alice.creates_a_pod(named=run.name("edited"))
        await alice.opens_pod_to(pod, who="ORG_MEMBERS")
        bob = await world.person("sofia")
        await alice.adds(bob, to_pod=pod, as_role="POD_EDITOR")

        refusal = await bob.is_refused_making_new_members_join(pod)

        # Not the open-policy refusal -- the pod is open -- but the editor's
        # authority: deciding who joins is managing the pod's members.
        assert refusal == "INSUFFICIENT_PERMISSION", refusal
        assert (await alice.opens_pod(pod))["config"]["auto_join_new_members"] is False

    @scenario("Someone who joins the organization is put in the pods set to take them in")
    @proves("PS-POD-024")
    @covers(
        "org.create",
        "org.invitation.invite",
        "org.invitation.accept",
        "pod.create",
        "pod.update",
        "pod.list",
        "pod.list_joinable",
        "pod.member.list",
    )
    @open_signup
    async def test_a_newcomer_joins_the_pods_set_to_take_them_in(self, world, run):
        # A fresh organization: a pod that takes in everyone who arrives would
        # otherwise sweep every later run's newcomers into it.
        needs(OPEN_SIGNUP)
        founder = await world.new_person("founder")
        organization = await founder.creates_an_organization()
        lobby = await founder.creates_a_pod(
            named=run.name("lobby"),
            settings={"join_policy": "ORG_MEMBERS", "auto_join_new_members": True},
        )
        early = await world.new_person("early")
        await early.accepts(await founder.invites(early, to=organization))
        # Being in the lobby is also how this knows their arrival has been
        # dealt with, before the next pod starts taking people in.
        await early.ends_up_in(lobby)

        room = await founder.creates_a_pod(named=run.name("room"))
        await founder.opens_pod_to(room, who="ORG_MEMBERS")
        await founder.makes_new_members_join(room)
        elsewhere = await founder.creates_a_pod(named=run.name("open"))
        await founder.opens_pod_to(elsewhere, who="ORG_MEMBERS")
        newcomer = await world.new_person("newcomer")
        await newcomer.accepts(await founder.invites(newcomer, to=organization))

        await newcomer.ends_up_in(lobby)
        await newcomer.ends_up_in(room)
        seat = await founder.membership_of(newcomer, in_pod=room)
        assert seat["roles"] == ["POD_USER"], seat
        offered = {
            str(pod["id"]) for pod in await newcomer.pods_they_can_join(organization)
        }
        assert str(elsewhere["id"]) in offered, (
            "an open pod that does not take people in is no longer theirs to choose"
        )
        # Already in the organization when the room started taking people in.
        await early.does_not_see_pod(room)


@scenario("Changing one pod setting leaves the others alone")
@proves("PS-POD-003")
@covers("pod.update", "pod.get")
async def test_a_partial_update_leaves_the_rest_of_the_settings(world, run):
    """A settings write is a merge, not a replace.

    `PS-POD-003` read `covered` on the strength of an icon upload, which says
    nothing about what happens to the settings a request did not mention. It
    matters because the interface sends one field at a time: a save that
    replaced the whole object would silently reset a pod's join policy every
    time somebody edited its description, and nothing would report it.
    """
    alice = await world.person("priya")
    pod = await alice.creates_a_pod(named=run.name("settings"))
    await alice.opens_pod_to(pod, who="ORG_MEMBERS")

    await alice.api.put(
        f"/pods/{pod['id']}",
        what="changing only the description",
        json={"description": "a pod whose join policy must survive this"},
    )

    reopened = await alice.opens_pod(pod)
    assert reopened.get("description") == "a pod whose join policy must survive this"
    assert (reopened.get("config") or {}).get("join_policy") == "ORG_MEMBERS", (
        f"changing the description reset the join policy: {reopened.get('config')}"
    )
