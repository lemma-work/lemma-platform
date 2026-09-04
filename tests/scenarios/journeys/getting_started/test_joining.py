"""Getting started → finding and joining an organization that already exists."""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Getting started"),
    capability("Join an organization that already exists"),
]


@scenario("A person cannot self-join an invite-only organization")
@proves("PS-ONB-031")
@covers("org.join_auto_join", "org.create")
async def test_invite_only_refuses_self_join(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    outsider = await world.new_person("outsider")

    response = await outsider.api.call(
        "POST", f"/organizations/{organization['id']}/join"
    )

    assert response.status_code == 403, (
        f"invite-only is the default; a stranger joined ({response.status_code}). "
        f"Asserted exactly rather than `>= 400`, which cannot tell a refusal "
        f"from the organization having quietly stopped existing"
    )


@scenario("A person with no work email is offered no organizations")
@proves("PS-ONB-030")
@covers("org.suggested")
async def test_suggestions_are_empty_without_a_matching_domain(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    stranger = await world.new_person("stranger")

    suggested = await stranger.api.get("/organizations/suggested")

    items = suggested if isinstance(suggested, list) else suggested.get("items", [])
    assert items == [], (
        f"a stranger was offered organizations they have no claim to: {items}"
    )


@scenario("A person checks whether a handle is free before taking it")
@proves("PS-ONB-011")
@covers("org.slug_availability")
async def test_handle_availability_is_checkable(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    taken = await alice.api.get(
        "/organizations/slug-availability", params={"slug": organization["slug"]}
    )
    free = await alice.api.get(
        "/organizations/slug-availability", params={"slug": "a-handle-nobody-has-taken"}
    )

    assert taken["available"] is False, taken
    assert free["available"] is True, free


@scenario("A person's profile follows them across organizations")
@proves("PS-ONB-004")
@covers("user.profile.upsert", "user.profile.get", "user.current.get")
async def test_a_profile_is_one_thing(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    await alice.creates_an_organization()

    await alice.sets_display_name("Ada", "Lovelace")

    profile = await alice.profile()
    assert profile.get("first_name") == "Ada", profile


@scenario("A person joins an organization that is open to everyone")
@proves("PS-ONB-031")
@covers("org.join_auto_join", "org.update", "org.member.list")
async def test_an_open_organization_admits_anyone_as_a_member(world):
    """The half of PS-ONB-031 nothing was checking.

    Only the refusal was covered, so the suite proved a stranger is kept out of
    an invite-only organization and said nothing about anybody ever getting in.
    A promise with only its unwanted clause tested is a promise that a
    permanently broken join would keep.
    """
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    await alice.api.patch(
        f"/organizations/{organization['id']}",
        what="opening the organization to everyone",
        json={"join_policy": "PUBLIC"},
    )
    newcomer = await world.new_person("newcomer")

    joined = await newcomer.api.call("POST", f"/organizations/{organization['id']}/join")

    assert joined.status_code < 400, (
        f"an organization open to everyone refused a signed-in person: "
        f"{joined.status_code} {joined.text[:200]}"
    )
    assert await newcomer.own_role_in(organization) == "ORG_MEMBER", (
        "somebody who walked in must arrive with the least-privileged role, "
        "not with whatever the organization hands its invitees"
    )


@scenario("Joining an organization twice leaves the first membership alone")
@proves("PS-ONB-031")
@covers("org.join_auto_join", "org.member.list")
async def test_joining_again_changes_nothing(world):
    """A retry must not quietly demote somebody.

    The join grants the least-privileged role, so a second call that re-ran the
    grant would take an editor back down to member — which is a privilege
    change nobody asked for, arriving through a button that reads as a no-op.
    """
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    await alice.api.patch(
        f"/organizations/{organization['id']}",
        what="opening the organization to everyone",
        json={"join_policy": "PUBLIC"},
    )

    again = await alice.api.call("POST", f"/organizations/{organization['id']}/join")

    assert again.status_code < 400, (
        f"re-joining answered {again.status_code}; it has to be safe to repeat"
    )
    assert await alice.own_role_in(organization) == "ORG_OWNER", (
        "joining again demoted the organization's own owner to a member"
    )
