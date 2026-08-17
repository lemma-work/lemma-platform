"""Getting started → bringing a team into the organization."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Getting started"), capability("Bring a team in")]


@pytest.fixture
async def owner(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice


@scenario("An invited person joins with the role they were offered")
@proves("PS-ONB-020")
@covers("org.invitation.invite", "org.invitation.accept", "organization.member_joined")
async def test_an_invited_person_joins_with_the_offered_role(world, owner):
    bob = await world.new_person("bob")

    invitation = await owner.invites(bob, to=owner.organization, as_role="ORG_EDITOR")
    await bob.accepts(invitation)

    assert await bob.own_role_in(owner.organization) == "ORG_EDITOR"


@scenario("An invitation is only usable by the person it was addressed to")
@proves("PS-ONB-020")
@covers("org.invitation.accept")
async def test_an_invitation_is_addressed(world, owner):
    bob = await world.new_person("bob")
    interloper = await world.new_person("interloper")

    invitation = await owner.invites(bob, to=owner.organization)

    await interloper.is_refused_invitation(invitation)


@scenario("A revoked invitation cannot be accepted")
@proves("PS-ONB-022")
@covers("org.invitation.revoke", "org.invitation.accept")
async def test_a_revoked_invitation_is_dead(world, owner):
    bob = await world.new_person("bob")
    invitation = await owner.invites(bob, to=owner.organization)

    await owner.revokes(invitation)

    await bob.is_refused_invitation(invitation)


@scenario("An invitation cannot be accepted twice")
@proves("PS-ONB-022")
@covers("org.invitation.accept")
async def test_an_invitation_is_single_use(world, owner):
    bob = await world.new_person("bob")
    invitation = await owner.invites(bob, to=owner.organization)
    await bob.accepts(invitation)

    await bob.is_refused_invitation(invitation)


@scenario("A person sees the invitations waiting for them")
@proves("PS-ONB-024")
@covers("org.invitation.list_mine")
async def test_a_person_sees_their_invitations(world, owner):
    bob = await world.new_person("bob")
    invitation = await owner.invites(bob, to=owner.organization)

    waiting = await bob.invitations()

    assert any(str(i["id"]) == str(invitation["id"]) for i in waiting), waiting


@scenario("Only an owner can change what the organization is")
@proves("PS-ONB-013")
@covers("org.update")
async def test_only_an_owner_changes_the_organization(world, owner):
    bob = await world.new_person("bob")
    await bob.accepts(await owner.invites(bob, to=owner.organization, as_role="ORG_MEMBER"))

    await bob.is_refused_renaming(owner.organization, to="Bob's Organization Now")


@scenario("Inviting somebody who is already inside is refused, not duplicated")
@proves("PS-ONB-023")
@covers("org.invitation.invite", "org.member.list")
async def test_inviting_an_existing_member_is_refused(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))

    response = await alice.api.call(
        "POST",
        f"/organizations/{organization['id']}/invitations",
        json={"email": bob.email, "role": "ORG_MEMBER"},
    )

    assert response.status_code >= 400, (
        f"inviting a member who is already in the organization was accepted "
        f"({response.status_code}), which leaves an invitation that can only "
        f"confuse whoever receives it"
    )
    # And the refusal changed nothing: bob is still a member, exactly once.
    members = [
        member
        for member in await alice.members_of(organization)
        if str(member.get("user_id")) == str(bob.user_id)
    ]
    assert len(members) == 1, f"bob's membership was disturbed: {members}"
