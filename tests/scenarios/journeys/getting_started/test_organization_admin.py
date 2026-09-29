"""Getting started → administering an organization's people."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, open_signup, proves, scenario

pytestmark = [
    journey("Getting started"),
    capability("Change and remove membership"),
    open_signup,
]


@pytest.fixture
async def org_with_member(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))
    return alice, bob, organization


@scenario("An owner changes what a member may do")
@proves("PS-ONB-040")
@covers("org.member.update_role", "org.member.list")
async def test_an_owner_changes_a_role(org_with_member):
    alice, bob, organization = org_with_member
    assert await bob.own_role_in(organization) == "ORG_MEMBER"

    await alice.changes_role(bob, to="ORG_EDITOR", in_organization=organization)

    assert await bob.own_role_in(organization) == "ORG_EDITOR"


@scenario("A member cannot change any role")
@proves("PS-ONB-040")
@covers("org.member.update_role")
async def test_a_member_cannot_change_roles(world, org_with_member):
    alice, bob, organization = org_with_member
    carol = await world.new_person("carol")
    await carol.accepts(await alice.invites(carol, to=organization))
    carols_membership = await alice.org_membership_of(carol, in_organization=organization)

    response = await bob.api.call(
        "PATCH",
        f"/organizations/{organization['id']}/members/{carols_membership['id']}/role",
        json={"role": "ORG_OWNER"},
    )

    assert response.status_code >= 400, (
        f"an ordinary member changed a role ({response.status_code})"
    )


@pytest.fixture
async def org_with_an_editor(world):
    """An owner, an editor, and two ordinary members: the whole hierarchy."""
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    dan = await world.new_person("dan")
    await dan.accepts(await alice.invites(dan, to=organization, as_role="ORG_EDITOR"))
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))
    carol = await world.new_person("carol")
    await carol.accepts(await alice.invites(carol, to=organization))
    return alice, dan, bob, carol, organization


@scenario("An editor changes roles up to their own level")
@proves("PS-ONB-040")
@covers("org.member.update_role", "org.member.list")
async def test_an_editor_changes_roles_up_to_their_own_level(world, org_with_an_editor):
    _alice, dan, bob, _carol, organization = org_with_an_editor

    await dan.changes_role(bob, to="ORG_EDITOR", in_organization=organization)
    assert await bob.own_role_in(organization) == "ORG_EDITOR"

    # Nothing about the promotion was a label: Bob can now do what an editor does.
    invitee = await world.new_person("erin")
    await bob.invites(invitee, to=organization)

    await dan.changes_role(bob, to="ORG_MEMBER", in_organization=organization)
    assert await bob.own_role_in(organization) == "ORG_MEMBER"


@scenario("An editor cannot give anyone, themselves included, the owner role")
@proves("PS-ONB-040")
@covers("org.member.update_role", "org.member.list")
async def test_an_editor_cannot_make_an_owner(org_with_an_editor):
    _alice, dan, bob, _carol, organization = org_with_an_editor

    await dan.is_refused_changing_role(bob, to="ORG_OWNER", in_organization=organization)
    await dan.is_refused_changing_role(dan, to="ORG_OWNER", in_organization=organization)

    assert await bob.own_role_in(organization) == "ORG_MEMBER"
    assert await dan.own_role_in(organization) == "ORG_EDITOR"


@scenario("An editor cannot demote or remove an owner")
@proves("PS-ONB-040", "PS-ONB-042")
@covers("org.member.update_role", "org.member.remove", "org.member.list")
async def test_an_editor_cannot_reach_over_an_owner(org_with_an_editor):
    alice, dan, _bob, _carol, organization = org_with_an_editor

    await dan.is_refused_changing_role(
        alice, to="ORG_MEMBER", in_organization=organization
    )
    await dan.is_refused_removing_from_organization(alice, organization=organization)

    assert await alice.own_role_in(organization) == "ORG_OWNER"


@scenario("An editor removes members and other editors")
@proves("PS-ONB-042")
@covers("org.member.remove", "org.member.list")
async def test_an_editor_removes_members_and_editors(org_with_an_editor):
    alice, dan, bob, carol, organization = org_with_an_editor
    await alice.changes_role(carol, to="ORG_EDITOR", in_organization=organization)

    await dan.removes_from_organization(bob, organization=organization)
    await dan.removes_from_organization(carol, organization=organization)

    remaining = {str(m["user_id"]) for m in await alice.members_of(organization)}
    assert str(bob.user_id) not in remaining and str(carol.user_id) not in remaining
    assert str(dan.user_id) in remaining and str(alice.user_id) in remaining


@scenario("A member manages nobody")
@proves("PS-ONB-040", "PS-ONB-042")
@covers("org.member.update_role", "org.member.remove", "org.invitation.invite")
async def test_a_member_manages_nobody(world, org_with_an_editor):
    _alice, dan, bob, carol, organization = org_with_an_editor
    frank = await world.new_person("frank")

    await bob.is_refused_inviting(frank, to=organization)
    await bob.is_refused_changing_role(
        carol, to="ORG_MEMBER", in_organization=organization
    )
    await bob.is_refused_removing_from_organization(carol, organization=organization)
    await bob.is_refused_removing_from_organization(dan, organization=organization)


@scenario("An owner removes a member and their access goes with them")
@proves("PS-ONB-042", "PS-ONB-043")
@covers("org.member.remove", "org.member.list", "org.list")
async def test_removing_a_member_takes_their_access(org_with_member):
    alice, bob, organization = org_with_member

    await alice.removes_from_organization(bob, organization=organization)

    remaining = {str(m["user_id"]) for m in await alice.members_of(organization)}
    assert str(bob.user_id) not in remaining, remaining
    mine = {str(o["id"]) for o in await bob.organizations()}
    assert str(organization["id"]) not in mine, (
        "a removed member must stop seeing the organization"
    )


@scenario("An owner sees the invitations their organization has sent")
@proves("PS-ONB-024")
@covers("org.invitation.list", "org.invitation.get")
async def test_an_owner_sees_sent_invitations(world, org_with_member):
    alice, _bob, organization = org_with_member
    carol = await world.new_person("carol")
    invitation = await alice.invites(carol, to=organization)

    sent = await alice.invitations_for(organization)
    assert any(str(i["id"]) == str(invitation["id"]) for i in sent), sent

    opened = await alice.opens_invitation(invitation)
    assert str(opened["id"]) == str(invitation["id"]), opened


@scenario("A person lands on a home view of their organization")
@proves("PS-POD-031")
@covers("org.home", "org.navigation")
async def test_an_organization_has_a_home(org_with_member):
    alice, _bob, organization = org_with_member
    await alice.creates_a_pod()

    home = await alice.home_of(organization)

    assert home is not None, home


@scenario("A credential resolves to the person it was issued to")
@proves("PS-ONB-003")
@covers("auth.verify_token", "user.current.get")
async def test_a_credential_identifies_its_owner(org_with_member):
    alice, bob, _organization = org_with_member

    mine = await alice.whoami()
    theirs = await bob.whoami()

    assert str(mine.get("user_id") or mine.get("id")) == str(alice.user_id), mine
    assert str(theirs.get("user_id") or theirs.get("id")) == str(bob.user_id), theirs


@scenario("A credential that is not genuine is refused, not treated as a stranger")
@proves("PS-ONB-003")
@covers("user.current.get")
async def test_a_forged_credential_is_refused(world):
    """The unwanted half of PS-ONB-003, and the one with teeth.

    "Shall not fall back to an anonymous identity" is the whole clause: a
    request carrying a bad token must be refused, not quietly downgraded to
    whatever an unauthenticated caller may see. The difference is invisible on
    an endpoint that answers strangers anyway, which is why this asserts the
    status rather than the body.

    Each of these fails differently on purpose — a token that is not a token,
    one shaped like a JWT but signed by nobody, and an empty bearer — because
    they take different paths through the auth stack and only one of them
    needs to leak for the clause to be broken.
    """
    alice = await world.person("priya")
    genuine = await alice.whoami()
    assert str(genuine.get("user_id") or genuine.get("id")) == str(alice.user_id)

    forgeries = {
        "not a token at all": "clearly-not-a-jwt",
        "shaped like a JWT, signed by nobody": (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAifQ"
            ".c2lnbmF0dXJlLXRoYXQtd2FzLW5ldmVyLWlzc3VlZA"
        ),
        # Not an empty bearer: `Authorization: Bearer ` is an illegal header
        # value and httpx refuses to send it, so the case never reaches Lemma.
        "a token with the right shape and a wrong key id": (
            "eyJraWQiOiJuby1zdWNoLWtleSIsImFsZyI6IlJTMjU2In0"
            ".eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAifQ"
            ".bm90LWEtcmVhbC1zaWduYXR1cmU"
        ),
    }
    for description, token in forgeries.items():
        answer = await alice.api.call(
            "GET", "/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert answer.status_code == 401, (
            f"{description} answered {answer.status_code}, not 401. A request "
            f"the platform cannot attribute has to be refused rather than "
            f"served as somebody: {answer.text[:200]}"
        )
