"""Who may put people into a pod, and at what level, over real HTTP.

Adding, re-roling and removing pod members, approving a join request, and
inviting somebody to a pod all answer to one rule (PS-POD-010, PS-POD-013,
PS-POD-022): the actor holds ``pod.member.manage`` -- or owns the organization --
and nothing they confer or remove sits outside what they hold.

Three things this file exists to keep true, each of which was once false:

- An organization owner who holds no pod role may administer the pod's members.
  A role-name gate in front of the permission check refused them the pod they
  own.
- A custom role carrying ``pod.member.manage`` may administer members within
  its bounds. The same gate refused every holder whose role was not literally
  POD_ADMIN, while the last-administrator rule counted them as administrators.
- An organization *editor* has no authority inside a pod they are not a member of,
  and so cannot mint an administrator there by approving a join request -- their
  own included -- or by inviting a second address with a pod role.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def _pod(owner: AsyncClient, org_id: str, name: str) -> str:
    response = await owner.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"{name} {uuid4().hex[:8]}",
            "description": "member authority e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _org_person(
    owner: AsyncClient, anon: AsyncClient, org_id: str, prefix: str, org_role: str
) -> tuple[dict, dict]:
    """A new organization member holding ``org_role``; their login and member row."""
    user = await signup_user(anon, prefix)
    row = await invite_org_member(owner, anon, org_id=org_id, user=user)
    if org_role != "ORG_MEMBER":
        promoted = await owner.patch(
            f"/organizations/{org_id}/members/{row['id']}/role",
            json={"role": org_role},
        )
        assert promoted.status_code == status.HTTP_200_OK, promoted.text
    return user, row


@pytest.mark.asyncio
async def test_an_organization_owner_administers_the_members_of_a_pod_they_do_not_belong_to(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id, "Owner reach")
    # Owns the organization; holds no role in this pod.
    owner2, _ = await _org_person(
        authenticated_client, async_client, org_id, "owner2", "ORG_OWNER"
    )
    _, colleague_row = await _org_person(
        authenticated_client, async_client, org_id, "colleague", "ORG_MEMBER"
    )
    headers = auth_headers(owner2)

    added = await async_client.post(
        f"/pods/{pod_id}/members",
        json={"organization_member_id": colleague_row["id"], "roles": ["POD_USER"]},
        headers=headers,
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text
    member_id = added.json()["pod_member_id"]

    promoted = await async_client.patch(
        f"/pods/{pod_id}/members/{member_id}/roles",
        json={"roles": ["POD_ADMIN"]},
        headers=headers,
    )
    assert promoted.status_code == status.HTTP_200_OK, promoted.text
    assert promoted.json()["roles"] == ["POD_ADMIN"]

    removed = await async_client.delete(
        f"/pods/{pod_id}/members/{member_id}", headers=headers
    )
    assert removed.status_code == status.HTTP_204_NO_CONTENT, removed.text


@pytest.mark.asyncio
async def test_a_custom_member_manager_administers_within_their_bounds(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """A viewer who also holds a role carrying ``pod.member.manage``.

    Not literally POD_ADMIN, so a role-name gate refused them everything.
    """
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id, "Custom manager")
    role = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": "roster_keeper", "permission_ids": ["pod.member.manage"]},
    )
    assert role.status_code == status.HTTP_201_CREATED, role.text

    keeper, keeper_row = await _org_person(
        authenticated_client, async_client, org_id, "keeper", "ORG_MEMBER"
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=keeper_row["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER", "ROSTER_KEEPER"],
    )
    hire, hire_row = await _org_person(
        authenticated_client, async_client, org_id, "hire", "ORG_MEMBER"
    )
    headers = auth_headers(keeper)

    added = await async_client.post(
        f"/pods/{pod_id}/members",
        json={"organization_member_id": hire_row["id"], "roles": ["POD_VIEWER"]},
        headers=headers,
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text

    # Beyond what they hold: the bound refuses, with a code a client can branch on.
    too_far = await async_client.patch(
        f"/pods/{pod_id}/members/{added.json()['pod_member_id']}/roles",
        json={"roles": ["POD_ADMIN"]},
        headers=headers,
    )
    assert too_far.status_code == status.HTTP_403_FORBIDDEN, too_far.text
    assert too_far.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER"

    removed = await async_client.delete(
        f"/pods/{pod_id}/members/{added.json()['pod_member_id']}", headers=headers
    )
    assert removed.status_code == status.HTTP_204_NO_CONTENT, removed.text


@pytest.mark.asyncio
async def test_a_manager_cannot_remove_or_demote_somebody_who_holds_more_than_they_do(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """Taking authority away is the mirror of granting it."""
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id, "Reach over")
    role = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={"name": "roster_keeper", "permission_ids": ["pod.member.manage"]},
    )
    assert role.status_code == status.HTTP_201_CREATED, role.text
    keeper, keeper_row = await _org_person(
        authenticated_client, async_client, org_id, "keeper", "ORG_MEMBER"
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=keeper_row["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR", "ROSTER_KEEPER"],
    )
    _, admin_row = await _org_person(
        authenticated_client, async_client, org_id, "admin2", "ORG_MEMBER"
    )
    admin = await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=admin_row["id"],
        role="POD_ADMIN",
    )
    headers = auth_headers(keeper)

    removed = await async_client.delete(
        f"/pods/{pod_id}/members/{admin['pod_member_id']}", headers=headers
    )
    assert removed.status_code == status.HTTP_403_FORBIDDEN, removed.text
    assert removed.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER"
    demoted = await async_client.patch(
        f"/pods/{pod_id}/members/{admin['pod_member_id']}/roles",
        json={"roles": ["POD_VIEWER"]},
        headers=headers,
    )
    assert demoted.status_code == status.HTTP_403_FORBIDDEN, demoted.text

    still_there = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert admin["pod_member_id"] in {
        m["pod_member_id"] for m in still_there.json()["items"]
    }


@pytest.mark.asyncio
async def test_an_organization_editor_cannot_mint_a_pod_administrator(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """An editor manages *people in the organization*, not the pods in it.

    They cannot open this pod, so they cannot decide who joins it: not a
    stranger's request, not their own, and not by inviting a second address with
    a pod role.
    """
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id, "Editor reach")
    editor, _ = await _org_person(
        authenticated_client, async_client, org_id, "editor", "ORG_EDITOR"
    )
    requester, _ = await _org_person(
        authenticated_client, async_client, org_id, "requester", "ORG_MEMBER"
    )
    headers = auth_headers(editor)

    assert (
        await async_client.get(f"/pods/{pod_id}", headers=headers)
    ).status_code == status.HTTP_403_FORBIDDEN

    # Their own request, approved by themselves as administrator.
    own = await async_client.post(f"/pods/{pod_id}/join-requests", headers=headers)
    assert own.status_code == status.HTTP_201_CREATED, own.text
    self_approval = await async_client.post(
        f"/pods/{pod_id}/join-requests/{own.json()['id']}/approve",
        json={"org_role": "ORG_MEMBER", "pod_role": "POD_ADMIN"},
        headers=headers,
    )
    assert self_approval.status_code == status.HTTP_403_FORBIDDEN, self_approval.text

    # Somebody else's, at any level.
    theirs = await async_client.post(
        f"/pods/{pod_id}/join-requests", headers=auth_headers(requester)
    )
    assert theirs.status_code == status.HTTP_201_CREATED, theirs.text
    approval = await async_client.post(
        f"/pods/{pod_id}/join-requests/{theirs.json()['id']}/approve",
        json={"org_role": "ORG_MEMBER", "pod_role": "POD_USER"},
        headers=headers,
    )
    assert approval.status_code == status.HTTP_403_FORBIDDEN, approval.text
    listing = await async_client.get(f"/pods/{pod_id}/join-requests", headers=headers)
    assert listing.status_code == status.HTTP_403_FORBIDDEN, listing.text

    # An invitation that carries a pod role is a pod grant that lands later.
    invitation = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={
            "email": f"test+ghost-{uuid4().hex[:8]}@example.com",
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
            "pod_role": "POD_ADMIN",
        },
        headers=headers,
    )
    assert invitation.status_code == status.HTTP_403_FORBIDDEN, invitation.text

    # Still nobody in the pod who was not put there by somebody who may.
    assert (
        await async_client.get(f"/pods/{pod_id}", headers=headers)
    ).status_code == status.HTTP_403_FORBIDDEN
    members = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert len(members.json()["items"]) == 1, members.text


@pytest.mark.asyncio
async def test_an_editor_who_administers_a_pod_invites_to_it_within_their_role(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """The other half: bound by the pod role held, not by the organization role."""
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id, "Editor admin")
    editor, editor_row = await _org_person(
        authenticated_client, async_client, org_id, "editor", "ORG_EDITOR"
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=editor_row["id"],
        role="POD_ADMIN",
    )

    invitation = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={
            "email": f"test+hire-{uuid4().hex[:8]}@example.com",
            "role": "ORG_MEMBER",
            "pod_id": pod_id,
            "pod_role": "POD_ADMIN",
        },
        headers=auth_headers(editor),
    )
    assert invitation.status_code == status.HTTP_201_CREATED, invitation.text
