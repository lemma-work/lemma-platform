"""Getting started → Sign up and create an organization.

Proves the promises in
[docs/product/journeys/getting-started.md](../../../../docs/product/journeys/getting-started.md).
"""

from __future__ import annotations

from harness import capability, covers, journey, open_signup, proves, scenario

pytestmark = [
    journey("Getting started"),
    capability("Sign up and create an organization"),
    open_signup,
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


@scenario("Asking for no private pod still lands a person in their organization's Lemma")
@proves("PS-ONB-050")
@covers("users.ensure_first_workspace")
async def test_no_private_pod_is_made_beside_the_organizations_lemma(world):
    """What the web asks for: the organization's Lemma, and nothing private.

    A later request that does ask for a private pod finds the Lemma already
    there and makes nothing beside it.
    """
    person = await world.new_person("import_then_chat")
    imported = await person.prepares_first_workspace(with_pod=False)
    assert imported["pod_id"] == imported["organization_pod_id"], imported
    assert imported["pod_id"] and imported["assistant_id"]
    ready = await person.prepares_first_workspace()
    assert ready["organization_id"] == imported["organization_id"]
    assert ready["pod_id"] == imported["pod_id"]
    assert not ready["pod_created"]


@scenario("A new organization opens in a Lemma everyone in it shares")
@proves("PS-ONB-015")
@covers(
    "users.ensure_first_workspace",
    "pod.get",
    "pod.list",
    "org.invitation.invite",
    "org.invitation.accept",
    "org.permissions.me",
)
async def test_a_new_organization_opens_in_a_lemma_everyone_shares(world):
    founder = await world.new_person("founder")

    made = await founder.prepares_first_workspace(with_pod=False)

    assert made["organization_created"], made
    # The suite's addresses are unverified, so this is a workspace of one,
    # named after the person, and its Lemma is theirs.
    assert made["organization_name"] == "Founder's workspace", made
    assert made["organization_open_to_domain"] is None, made
    assert made["pod_id"] == made["organization_pod_id"], made
    assert made["pod_name"] == "Personal", made
    lemma = await founder.opens_pod({"id": made["pod_id"], "name": made["pod_name"]})
    assert lemma["config"]["join_policy"] == "ORG_MEMBERS", lemma["config"]
    assert lemma["config"]["auto_join_new_members"] is True, lemma["config"]
    organization = {"id": made["organization_id"], "name": made["organization_name"]}
    assert "org.member.manage" in await founder.org_permissions_in(organization), (
        "the person a first workspace makes an organization for owns it in "
        "every check, not only on the membership list"
    )

    colleague = await world.new_person("colleague")
    invitation = await founder.invites(colleague, to=organization)
    await colleague.accepts(invitation)
    arrived = await colleague.prepares_first_workspace(with_pod=False)

    assert arrived["organization_id"] == made["organization_id"], arrived
    assert arrived["pod_id"] == made["organization_pod_id"], arrived
    assert not arrived["organization_created"] and not arrived["pod_created"]
    await colleague.sees_pod(lemma)

    again = await founder.prepares_first_workspace(with_pod=False)
    assert (again["organization_id"], again["pod_id"]) == (
        made["organization_id"],
        made["pod_id"],
    )
    assert [pod["id"] for pod in await founder.pods_in(organization)] == [
        made["pod_id"]
    ], "asking again made a second Lemma"


@scenario("What a person does is asked once and stays with them")
@proves("PS-ONB-008")
@covers("users.work.set", "user.current.get")
async def test_what_a_person_does_stays_with_them(world):
    person = await world.new_person("asked")
    assert (await person.profile())["work"] is None, "nobody has asked them yet"

    answered = await person.says_what_they_do(["PRODUCT", "FOUNDER"], other=" Design ")

    assert answered["work"]["jobs"] == ["PRODUCT", "FOUNDER"], answered["work"]
    assert answered["work"]["other"] == "Design", answered["work"]
    assert (await person.profile())["work"] == answered["work"]

    skipper = await world.new_person("skipper")
    skipped = await skipper.says_what_they_do([])
    assert skipped["work"]["jobs"] == [] and skipped["work"]["other"] is None
    assert skipped["work"]["answered_at"], "a skip still records they were asked"
