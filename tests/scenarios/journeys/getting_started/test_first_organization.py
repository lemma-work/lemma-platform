"""Getting started → Sign up and create an organization.

Proves the promises in
[docs/product/journeys/getting-started.md](../../../../docs/product/journeys/getting-started.md).
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Getting started"),
    capability("Sign up and create an organization"),
]


@scenario("A new person signs up and becomes a known user")
@proves("PS-ONB-001")
@covers("user.current.get", "auth.signed_up")
async def test_new_person_signs_up_and_is_known(world):
    # pool=False: the whole point of this scenario is the signup act itself —
    # a reused pool account would prove nothing.
    alice = await world.new_person("alice", pool=False)

    profile = await alice.profile()

    assert profile["email"] == alice.email, profile
    assert str(profile["id"]) == str(alice.user_id), profile


@scenario("A person who has joined nothing sees an empty start, not an error")
@proves("PS-ONB-002")
@covers("org.list", "org.navigation", "user.current.get", "user.profile.get")
async def test_person_with_no_organization_sees_an_empty_start(world):
    # pool=False: this asserts the account has joined literally nothing, which
    # only a genuinely fresh account can promise — a reused pool account may
    # carry organizations from an earlier run.
    newcomer = await world.new_person("newcomer", pool=False)

    await newcomer.belongs_to_no_organization()

    # The point of this scenario is that none of these are errors. A person who
    # has signed up and done nothing else is a normal state, not a broken one.
    await newcomer.navigation()
    await newcomer.profile()


@scenario("The person who creates an organization owns it")
@proves("PS-ONB-010")
@covers("org.create", "org.member.list", "organization.created")
async def test_creator_of_an_organization_owns_it(world):
    alice = await world.new_person("alice")

    organization = await alice.creates_an_organization()

    assert await alice.own_role_in(organization) == "ORG_OWNER"


@scenario("A person can belong to more than one organization")
@proves("PS-ONB-010")
@covers("org.create", "org.list")
async def test_a_person_can_own_several_organizations(world):
    alice = await world.new_person("alice")

    first = await alice.creates_an_organization()
    second = await alice.creates_an_organization()

    mine = {str(organization["id"]) for organization in await alice.organizations()}
    assert {str(first["id"]), str(second["id"])} <= mine


@scenario("An organization keeps its handle when it is renamed")
@proves("PS-ONB-011")
@covers("org.create", "org.update", "org.get")
async def test_renaming_an_organization_keeps_its_handle(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    original_handle = organization["slug"]

    renamed = await alice.renames_organization(
        organization, to=f"{organization['name']} Renamed"
    )

    assert renamed["slug"] == original_handle, (
        "the handle is what links and references resolve through, so a rename "
        "must not move it"
    )
