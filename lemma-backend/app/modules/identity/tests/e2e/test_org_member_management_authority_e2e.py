"""Who may invite, re-role and remove organization members, over real HTTP.

The rule (PS-ONB-020, PS-ONB-040, PS-ONB-042) is one sentence: managing people
is what an organization editor is *for*, and nobody hands out -- or takes away --
authority they do not hold. So an editor may invite, re-role and remove, up to and
including their own level, and never an owner: not by making one, not by making
themselves one, not by removing or demoting one.

Every test acts as the person the rule is about. Refusals are checked against a
sibling action the same actor is allowed, because a bound that refuses
everything passes the refusal half on its own.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

pytestmark = pytest.mark.e2e


def _headers(user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


async def _join(
    owner: AsyncClient,
    anon: AsyncClient,
    org_id: str,
    user: dict[str, str],
    role: str,
) -> dict:
    """Bring ``user`` into the organization with ``role``; return their member row."""
    invite = await owner.post(
        f"/organizations/{org_id}/invitations",
        json={"email": user["email"], "role": role},
    )
    assert invite.status_code == status.HTTP_201_CREATED, invite.text
    accept = await anon.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=_headers(user),
    )
    assert accept.status_code == status.HTTP_200_OK, accept.text
    members = await owner.get(f"/organizations/{org_id}/members")
    return next(
        item
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == user["email"]
    )


async def _set_role(
    anon: AsyncClient, org_id: str, actor: dict, member: dict, role: str
):
    return await anon.patch(
        f"/organizations/{org_id}/members/{member['id']}/role",
        json={"role": role},
        headers=_headers(actor),
    )


@pytest.mark.asyncio
async def test_an_editor_invites_up_to_their_own_level_and_no_further(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    editor = await signup_user()
    await _join(authenticated_client, async_client, org_id, editor, "ORG_EDITOR")

    async def invite(role: str):
        return await async_client.post(
            f"/organizations/{org_id}/invitations",
            json={"email": f"test+inv-{uuid4().hex[:8]}@example.com", "role": role},
            headers=_headers(editor),
        )

    assert (await invite("ORG_MEMBER")).status_code == status.HTTP_201_CREATED
    assert (await invite("ORG_EDITOR")).status_code == status.HTTP_201_CREATED
    refused = await invite("ORG_OWNER")
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text


@pytest.mark.asyncio
async def test_an_editor_changes_roles_up_to_their_own_level_and_no_further(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    editor = await signup_user()
    editor_row = await _join(
        authenticated_client, async_client, org_id, editor, "ORG_EDITOR"
    )
    colleague = await signup_user()
    colleague_row = await _join(
        authenticated_client, async_client, org_id, colleague, "ORG_MEMBER"
    )

    promoted = await _set_role(
        async_client, org_id, editor, colleague_row, "ORG_EDITOR"
    )
    assert promoted.status_code == status.HTTP_200_OK, promoted.text
    assert promoted.json()["role"] == "ORG_EDITOR"

    demoted = await _set_role(async_client, org_id, editor, colleague_row, "ORG_MEMBER")
    assert demoted.status_code == status.HTTP_200_OK, demoted.text

    # Neither somebody else nor themselves, however the request is shaped.
    for target in (colleague_row, editor_row):
        overreach = await _set_role(async_client, org_id, editor, target, "ORG_OWNER")
        assert overreach.status_code == status.HTTP_403_FORBIDDEN, overreach.text

    members = await authenticated_client.get(f"/organizations/{org_id}/members")
    roles = {m["id"]: m["role"] for m in members.json()["items"]}
    assert roles[editor_row["id"]] == "ORG_EDITOR"
    assert roles[colleague_row["id"]] == "ORG_MEMBER"


@pytest.mark.asyncio
async def test_a_role_change_takes_effect_on_the_next_request(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    """An editor's promotion is real authority, not a label on the row.

    The promoted person can themselves invite, which they could not a moment
    earlier -- the authority is read from the role, not remembered.
    """
    org_id = fixed_test_org["id"]
    editor = await signup_user()
    await _join(authenticated_client, async_client, org_id, editor, "ORG_EDITOR")
    colleague = await signup_user()
    colleague_row = await _join(
        authenticated_client, async_client, org_id, colleague, "ORG_MEMBER"
    )

    def _invite():
        return async_client.post(
            f"/organizations/{org_id}/invitations",
            json={
                "email": f"test+inv-{uuid4().hex[:8]}@example.com",
                "role": "ORG_MEMBER",
            },
            headers=_headers(colleague),
        )

    assert (await _invite()).status_code == status.HTTP_403_FORBIDDEN
    await _set_role(async_client, org_id, editor, colleague_row, "ORG_EDITOR")
    assert (await _invite()).status_code == status.HTTP_201_CREATED


@pytest.mark.asyncio
async def test_an_editor_removes_members_and_editors_but_never_an_owner(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    editor = await signup_user()
    await _join(authenticated_client, async_client, org_id, editor, "ORG_EDITOR")
    second_owner = await signup_user()
    owner_row = await _join(
        authenticated_client, async_client, org_id, second_owner, "ORG_OWNER"
    )
    peer = await signup_user()
    peer_row = await _join(
        authenticated_client, async_client, org_id, peer, "ORG_EDITOR"
    )
    member = await signup_user()
    member_row = await _join(
        authenticated_client, async_client, org_id, member, "ORG_MEMBER"
    )

    reach_over = await async_client.delete(
        f"/organizations/{org_id}/members/{owner_row['id']}", headers=_headers(editor)
    )
    assert reach_over.status_code == status.HTTP_403_FORBIDDEN, reach_over.text
    demote_owner = await _set_role(
        async_client, org_id, editor, owner_row, "ORG_MEMBER"
    )
    assert demote_owner.status_code == status.HTTP_403_FORBIDDEN, demote_owner.text

    for row in (member_row, peer_row):
        removed = await async_client.delete(
            f"/organizations/{org_id}/members/{row['id']}", headers=_headers(editor)
        )
        assert removed.status_code == status.HTTP_204_NO_CONTENT, removed.text

    members = await authenticated_client.get(f"/organizations/{org_id}/members")
    remaining = {m["id"] for m in members.json()["items"]}
    assert owner_row["id"] in remaining
    assert member_row["id"] not in remaining and peer_row["id"] not in remaining


@pytest.mark.asyncio
async def test_a_plain_member_manages_nobody(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    member = await signup_user()
    await _join(authenticated_client, async_client, org_id, member, "ORG_MEMBER")
    other = await signup_user()
    other_row = await _join(
        authenticated_client, async_client, org_id, other, "ORG_MEMBER"
    )

    invite = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": f"test+inv-{uuid4().hex[:8]}@example.com", "role": "ORG_MEMBER"},
        headers=_headers(member),
    )
    assert invite.status_code == status.HTTP_403_FORBIDDEN, invite.text
    listing = await async_client.get(
        f"/organizations/{org_id}/invitations", headers=_headers(member)
    )
    assert listing.status_code == status.HTTP_403_FORBIDDEN, listing.text
    assert (
        await _set_role(async_client, org_id, member, other_row, "ORG_MEMBER")
    ).status_code == status.HTTP_403_FORBIDDEN
    removed = await async_client.delete(
        f"/organizations/{org_id}/members/{other_row['id']}", headers=_headers(member)
    )
    assert removed.status_code == status.HTTP_403_FORBIDDEN, removed.text


@pytest.mark.asyncio
async def test_a_person_outside_the_organization_manages_nobody(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    member = await signup_user()
    member_row = await _join(
        authenticated_client, async_client, org_id, member, "ORG_MEMBER"
    )
    stranger = await signup_user()

    invite = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": f"test+inv-{uuid4().hex[:8]}@example.com", "role": "ORG_MEMBER"},
        headers=_headers(stranger),
    )
    assert invite.status_code == status.HTTP_403_FORBIDDEN, invite.text
    assert (
        await _set_role(async_client, org_id, stranger, member_row, "ORG_EDITOR")
    ).status_code == status.HTTP_403_FORBIDDEN
    removed = await async_client.delete(
        f"/organizations/{org_id}/members/{member_row['id']}",
        headers=_headers(stranger),
    )
    assert removed.status_code == status.HTTP_403_FORBIDDEN, removed.text
