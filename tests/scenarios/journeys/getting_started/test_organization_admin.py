"""Getting started → administering an organization's people."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Getting started"),
    capability("Change and remove membership"),
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


@scenario("A member who is not an owner cannot change any role")
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
