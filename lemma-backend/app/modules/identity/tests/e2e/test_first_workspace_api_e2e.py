"""The endpoint the web onboarding calls instead of deciding this itself."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

#: Everything an owner may do in an organization, as `permissions/me` lists it.
_OWNER_PERMISSIONS = {
    "org.read",
    "org.update",
    "org.member.manage",
    "org.invitation.manage",
    "org.billing.manage",
    "org.installation.manage",
    "pod.create",
}


async def test_a_new_account_is_given_an_organization_and_its_lemma(
    authenticated_client,
):
    response = await authenticated_client.post("/users/me/first-workspace")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"]
    # `authenticated_client` is a fresh account in no organization and this
    # test seeds no domain match, so the branch is not a matter of opinion.
    assert body["entry"] == "new_org"
    assert body["organization_created"]
    # The fixture's address is on a work domain, so this is its company's
    # organization: named after it, open to it, and landed in its own Lemma.
    assert body["organization_name"] == "Example"
    assert body["organization_open_to_domain"] == "example.com"
    assert body["pod_id"] == body["organization_pod_id"]
    assert body["pod_name"] == "Example"
    assert body["assistant_id"]
    assert body["pod_created"]

    lemma = await authenticated_client.get(f"/pods/{body['pod_id']}")
    assert lemma.status_code == 200, lemma.text
    config = lemma.json()["config"]
    assert config["join_policy"] == "ORG_MEMBERS"
    assert config["auto_join_new_members"] is True


async def test_calling_it_twice_does_not_make_a_second_workspace(
    authenticated_client,
):
    """The web onboarding runs on every load of an empty account.

    A second call has to be free, or someone who refreshed would collect
    workspaces.
    """
    first = await authenticated_client.post("/users/me/first-workspace")
    second = await authenticated_client.post("/users/me/first-workspace")
    assert first.status_code == second.status_code == 200
    assert first.json()["organization_id"] == second.json()["organization_id"], (
        "a repeat call must return the same organization"
    )
    # "existing", not the "saved" this used to say: there was never a saved
    # selection to honour. The only thing that ever wrote one was this call
    # storing back the workspace it had just picked.
    assert second.json()["entry"] == "existing"
    assert first.json()["pod_id"] == second.json()["pod_id"]
    assert second.json()["organization_pod_id"] == first.json()["organization_pod_id"]
    assert second.json()["assistant_id"]
    assert not second.json()["pod_created"]
    assert not second.json()["organization_created"]


async def test_no_private_pod_is_made_beside_the_organizations_lemma(
    authenticated_client,
):
    """`with_pod: false` is what the web sends; a default call adds nothing.

    A new organization is made with the Lemma everyone in it shares, so there
    is somewhere to land without a private pod -- and a later call that does
    ask for one must not add a spare pod beside it.
    """
    response = await authenticated_client.post(
        "/users/me/first-workspace", json={"with_pod": False}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry"] == "new_org"
    assert body["pod_id"] == body["organization_pod_id"]
    assert body["pod_id"] and body["assistant_id"]

    again = await authenticated_client.post("/users/me/first-workspace")
    assert again.status_code == 200, again.text
    assert again.json()["organization_id"] == body["organization_id"]
    assert again.json()["pod_id"] == body["pod_id"]
    assert not again.json()["pod_created"]

    pods = await authenticated_client.get(
        f"/organizations/{body['organization_id']}/pods"
    )
    assert pods.status_code == 200, pods.text
    assert [pod["id"] for pod in pods.json()["items"]] == [body["pod_id"]]


async def test_concurrent_provisioning_returns_one_lemma(authenticated_client):
    results = await asyncio.gather(
        *[authenticated_client.post("/users/me/first-workspace") for _ in range(3)],
        return_exceptions=True,
    )
    assert all(not isinstance(result, BaseException) for result in results), results
    assert all(result.status_code == 200 for result in results)
    assert len({result.json()["pod_id"] for result in results}) == 1
    assert sum(result.json()["pod_created"] for result in results) == 1


async def test_public_endpoint_rejects_installation_organization_hints(
    authenticated_client,
):
    response = await authenticated_client.post(
        "/users/me/first-workspace",
        json={"arrived_through_organization_id": str(uuid4())},
    )
    assert response.status_code == 422


async def test_it_needs_a_session(async_client):
    response = await async_client.post("/users/me/first-workspace")
    assert response.status_code in (401, 403), response.text


async def test_the_second_person_from_a_domain_lands_in_the_first_ones_lemma(
    async_client, signup_user
):
    """The same rule the chat path follows, reached through HTTP."""
    domain = f"api-acme-{uuid4().hex[:8]}.com"
    first = await signup_user(email=f"ada@{domain}")
    second = await signup_user(email=f"grace@{domain}")

    one = await async_client.post(
        "/users/me/first-workspace",
        headers=auth_headers(first),
        json={"with_pod": False},
    )
    two = await async_client.post(
        "/users/me/first-workspace",
        headers=auth_headers(second),
        json={"with_pod": False},
    )
    assert one.status_code == two.status_code == 200, two.text
    assert two.json()["entry"] == "domain_join"
    assert one.json()["organization_id"] == two.json()["organization_id"]
    assert two.json()["pod_id"] == one.json()["organization_pod_id"]
    assert two.json()["organization_pod_id"] == one.json()["organization_pod_id"]
    assert not two.json()["pod_created"]

    # They are in it in the answer, not once the arrival handler gets round
    # to it: the pod's own member route lets them in.
    members = await async_client.get(
        f"/pods/{two.json()['pod_id']}/members", headers=auth_headers(second)
    )
    assert members.status_code == 200, members.text
    assert second["id"] in {str(item["user_id"]) for item in members.json()["items"]}


async def test_the_organization_it_makes_is_owned_in_every_check(
    async_client, signup_user
):
    """An organization permission is read from role assignments alone.

    The membership row says who is owner; the assignment is what a permission
    check reads. An organization made here wrote the row and not the
    assignment, so its owner was refused everything an owner may do -- and a
    colleague who joined it held nothing either.
    """
    domain = f"roles-{uuid4().hex[:8]}.com"
    founder = await signup_user(email=f"ada@{domain}")
    colleague = await signup_user(email=f"grace@{domain}")

    made = await async_client.post(
        "/users/me/first-workspace",
        headers=auth_headers(founder),
        json={"with_pod": False},
    )
    assert made.status_code == 200, made.text
    organization_id = made.json()["organization_id"]
    joined = await async_client.post(
        "/users/me/first-workspace",
        headers=auth_headers(colleague),
        json={"with_pod": False},
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["organization_id"] == organization_id

    async def holds(person: dict[str, str]) -> set[str]:
        answer = await async_client.get(
            f"/organizations/{organization_id}/permissions/me",
            headers=auth_headers(person),
        )
        assert answer.status_code == 200, answer.text
        return set(answer.json()["actions"])

    assert await holds(founder) == _OWNER_PERMISSIONS
    assert await holds(colleague) == {"org.read"}


async def test_accepting_an_invitation_puts_a_newcomer_in_the_organizations_lemma(
    async_client, signup_user
):
    """Accepted from Home rather than at sign-up, an invitation still leaves
    them in the Lemma everyone in the organization shares, in the answer --
    so the screen that lists the Lemmas they can join shows them in it."""
    owner = await signup_user(email=f"ada@api-inv-{uuid4().hex[:8]}.com")
    made = await async_client.post(
        "/users/me/first-workspace", headers=auth_headers(owner), json={}
    )
    assert made.status_code == 200, made.text
    organization_id = made.json()["organization_id"]
    shared_pod_id = made.json()["organization_pod_id"]

    invitee = await signup_user()
    invite = await async_client.post(
        f"/organizations/{organization_id}/invitations",
        headers=auth_headers(owner),
        json={"email": invitee["email"], "role": "ORG_MEMBER"},
    )
    assert invite.status_code == 201, invite.text
    accepted = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=auth_headers(invitee),
    )
    assert accepted.status_code == 200, accepted.text

    members = await async_client.get(
        f"/pods/{shared_pod_id}/members", headers=auth_headers(invitee)
    )
    assert members.status_code == 200, members.text
    assert invitee["id"] in {str(item["user_id"]) for item in members.json()["items"]}
