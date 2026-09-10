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
    alice = await world.new_person("alice")

    profile = await alice.profile()

    assert profile["email"] == alice.email, profile
    assert str(profile["id"]) == str(alice.user_id), profile


@scenario("A person who signed up comes back and signs in")
@proves("PS-ONB-001")
@covers("user.current.get")
async def test_a_person_comes_back_and_signs_in(world):
    """The half of "sign up and sign in" that nothing used to prove.

    Every person this suite had ever made was brand new, so the product's most
    repeated action — somebody who already has an account coming back — was the
    one action no scenario exercised. The specification also promises the
    address is matched case-insensitively, which is checked here rather than
    taken on trust: `Ada@…` signing in as `ADA@…` has to be the same person and
    not a second account quietly created alongside the first.
    """
    ada = await world.new_person("Ada")

    returning = await world.returning(ada, using=ada.email.upper())

    assert str(returning.user_id) == str(ada.user_id), (
        f"signing in as {ada.email.upper()} reached user {returning.user_id}, "
        f"but signing up as {ada.email} made user {ada.user_id} — an address's "
        f"case decided who you are"
    )
    assert str((await returning.profile())["id"]) == str(ada.user_id)


@scenario("A person who has joined nothing sees an empty start, not an error")
@proves("PS-ONB-002")
@covers("org.list", "org.navigation", "user.current.get", "user.profile.get")
async def test_person_with_no_organization_sees_an_empty_start(world):
    newcomer = await world.new_person("newcomer")

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


@scenario("First-chat setup is idempotent and includes a personal assistant")
@proves("PS-ONB-050")
@covers("users.ensure_first_workspace")
async def test_first_chat_workspace_is_ready_and_reused(world):
    import asyncio

    person = await world.new_person("first_chat")
    first, second = await asyncio.gather(
        person.prepares_first_workspace(), person.prepares_first_workspace()
    )
    assert first["organization_id"] == second["organization_id"]
    assert first["pod_id"] == second["pod_id"]
    assert first["assistant_id"] == second["assistant_id"]
    assert first["pod_id"] and first["assistant_id"]
    assert sum(result["pod_created"] for result in (first, second)) == 1


@scenario("An importer can prepare only the organization and add chat later")
@proves("PS-ONB-050")
@covers("users.ensure_first_workspace")
async def test_importer_can_defer_personal_pod_creation(world):
    person = await world.new_person("import_then_chat")
    imported = await person.prepares_first_workspace(with_pod=False)
    assert imported["pod_id"] is None and imported["assistant_id"] is None
    ready = await person.prepares_first_workspace()
    assert ready["organization_id"] == imported["organization_id"]
    assert ready["pod_id"] and ready["assistant_id"]
