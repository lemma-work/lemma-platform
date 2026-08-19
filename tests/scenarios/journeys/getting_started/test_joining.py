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

    assert response.status_code >= 400, (
        f"invite-only is the default; a stranger joined ({response.status_code})"
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
