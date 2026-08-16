"""Building a pod → creating one, and deciding who is in it.

Proves promises in
[docs/product/journeys/building-a-pod.md](../../../../docs/product/journeys/building-a-pod.md).
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Building a pod"), capability("Create a pod")]


@scenario("A member of an organization creates a pod and administers it")
@proves("PS-POD-001")
@covers("pod.create", "pod.member.list", "pod.created")
async def test_pod_creator_administers_it(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()

    pod = await alice.creates_a_pod()

    members = await alice.members_of_pod(pod)
    mine = next(m for m in members if str(m["user_id"]) == str(alice.user_id))
    assert "POD_ADMIN" in mine["roles"], mine


@scenario("Someone outside the organization cannot create a pod in it")
@proves("PS-POD-001")
@covers("pod.create")
async def test_outsider_cannot_create_a_pod(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    outsider = await world.new_person("outsider")

    response = await outsider.api.call(
        "POST",
        "/pods",
        json={
            "organization_id": str(organization["id"]),
            "name": "Trespass",
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
async def test_pod_names_are_unique_within_an_organization(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    await alice.creates_a_pod(named="Support")

    response = await alice.api.call(
        "POST",
        "/pods",
        json={
            "organization_id": str(alice.organization["id"]),
            "name": "Support",
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
async def test_a_pod_name_is_scoped_to_its_organization(world):
    alice = await world.new_person("alice")
    first_org = await alice.creates_an_organization()
    await alice.creates_a_pod(in_organization=first_org, named="Support")

    second_org = await alice.creates_an_organization()
    elsewhere = await alice.creates_a_pod(in_organization=second_org, named="Support")

    assert elsewhere["name"] == "Support"
    assert str(elsewhere["organization_id"]) == str(second_org["id"])


class TestPuttingPeopleInAPod:
    pytestmark = capability("Put people in a pod")

    @scenario("A pod admin adds an organization member to the pod")
    @proves("PS-POD-010")
    @covers("pod.member.add", "pod.member.list", "pod.member_joined")
    async def test_admin_adds_an_organization_member(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()

        bob = await world.new_person("bob")
        invitation = await alice.invites(bob, to=organization)
        await bob.accepts(invitation)

        await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

        membership = await alice.membership_of(bob, in_pod=pod)
        assert membership["roles"] == ["POD_VIEWER"], membership

    @scenario("A pod viewer can read the pod but cannot write to it")
    @proves("PS-POD-011")
    @covers("pod.member.add", "pod.permissions.me", "pod.get")
    async def test_a_viewer_reads_but_does_not_write(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()

        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))
        await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

        await bob.can_read(pod)
        await bob.cannot_write_to(pod)

    @scenario("Someone with no pod membership cannot open the pod")
    @proves("PS-POD-030")
    @covers("pod.get", "pod.list")
    async def test_a_non_member_cannot_open_the_pod(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()

        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))

        # In the organization, but not in this pod.
        await bob.is_refused_pod(pod)
        await bob.does_not_see_pod(pod)

    @scenario("A person outside the organization cannot open the pod")
    @proves("PS-POD-030")
    @covers("pod.get")
    async def test_an_outsider_cannot_open_the_pod(self, world):
        alice = await world.new_person("alice")
        await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        outsider = await world.new_person("outsider")

        await outsider.is_refused_pod(pod)

    @scenario("Changing someone's role changes what they may do, immediately")
    @proves("PS-POD-011")
    @covers("pod.member.update_roles", "pod.permissions.me")
    async def test_a_role_change_applies_to_the_next_request(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))
        await alice.adds(bob, to_pod=pod, as_role="POD_VIEWER")

        as_viewer = await bob.permissions_in(pod)
        assert "datastore.table.create" not in as_viewer, as_viewer

        await alice.gives(bob, roles=["POD_EDITOR"], in_pod=pod)

        # Polled, not slept: the cached snapshot is invalidated after the
        # mutating request commits, so one stale read is possible. The promise
        # is that this lands promptly — the cache TTL is five minutes, and a
        # fifteen-second bound proves it is not waiting for that.
        as_editor = await bob.permissions_settle_to(
            lambda held: held > as_viewer,
            in_pod=pod,
            describe="widen to an editor's",
        )
        assert "datastore.table.create" in as_editor, as_editor

    @scenario("Removing someone from a pod takes their access away immediately")
    @proves("PS-POD-040")
    @covers("pod.member.remove", "pod.get")
    async def test_removing_a_member_revokes_access(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))
        await alice.adds(bob, to_pod=pod, as_role="POD_EDITOR")
        await bob.can_read(pod)

        membership = await alice.membership_of(bob, in_pod=pod)
        await alice.removes_member(membership, from_pod=pod)

        await bob.is_refused_pod(pod)

    @scenario("A pod member who is not an admin cannot remove other members")
    @proves("PS-POD-040")
    @covers("pod.member.remove")
    async def test_a_non_admin_cannot_remove_members(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        bob = await world.new_person("bob")
        carol = await world.new_person("carol")
        for person in (bob, carol):
            await person.accepts(await alice.invites(person, to=organization))
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
    async def test_a_new_pod_is_invite_only(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))

        await bob.is_refused_joining(pod)

    @scenario("A pod open to the organization lets any member walk in")
    @proves("PS-POD-020")
    @covers("pod.update", "pod.join", "pod.member_joined")
    async def test_an_org_open_pod_admits_members(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        await alice.opens_pod_to(pod, who="ORG_MEMBERS")

        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))
        await bob.joins(pod)

        await bob.can_read(pod)
        membership = await alice.membership_of(bob, in_pod=pod)
        assert membership["roles"] == ["POD_USER"], (
            "self-joining grants the base role, not a chosen one"
        )

    @scenario("Someone outside the organization cannot walk into an org-open pod")
    @proves("PS-POD-020")
    @covers("pod.update", "pod.join")
    async def test_an_outsider_cannot_join_an_org_open_pod(self, world):
        alice = await world.new_person("alice")
        await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        await alice.opens_pod_to(pod, who="ORG_MEMBERS")
        outsider = await world.new_person("outsider")

        await outsider.is_refused_joining(pod)

    @scenario("A person asks for access and an admin grants it")
    @proves("PS-POD-021")
    @covers("pod.join_request.create", "pod.join_request.list", "pod.join_request.approve")
    async def test_a_join_request_is_approved(self, world):
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))

        await bob.requests_to_join(pod)
        pending = await alice.join_requests_for(pod)
        assert len(pending) == 1, pending

        await alice.approves(pending[0], for_pod=pod, as_role="POD_USER")

        await bob.can_read(pod)
