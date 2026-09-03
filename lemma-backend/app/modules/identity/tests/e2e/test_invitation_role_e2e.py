"""An accepted invitation grants the role it offered (PS-ONB-020).

The membership row already recorded ``invitation.role``; the role *assignment*
the authorizer reads was hardcoded to ORG_MEMBER. The two stores disagreed, and
the disagreement is only visible from outside through a Context-based check --
which is why this drives the org-owner shortcut onto a pod the invitee is not a
member of, rather than reading the members list back.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

pytestmark = pytest.mark.e2e


def _headers(user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


@pytest.mark.asyncio
async def test_accepting_an_owner_invitation_confers_owner_authority(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    pod = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Invited Owner Pod {uuid4().hex[:8]}",
            "description": "invitation role e2e",
            "type": "HYBRID",
        },
    )
    assert pod.status_code == status.HTTP_201_CREATED, pod.text
    pod_id = pod.json()["id"]

    invitee = await signup_user()
    invite = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": invitee["email"], "role": "ORG_OWNER"},
    )
    assert invite.status_code == status.HTTP_201_CREATED, invite.text

    accept = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=_headers(invitee),
    )
    assert accept.status_code == status.HTTP_200_OK, accept.text

    members = await authenticated_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == status.HTTP_200_OK, members.text
    invited = next(
        item
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == invitee["email"]
    )
    assert invited["role"] == "ORG_OWNER"

    # The authority behind that row: an org owner reaches every pod in their
    # organization without a pod membership. This 403'd while the assignment
    # said ORG_MEMBER.
    reached = await async_client.get(f"/pods/{pod_id}", headers=_headers(invitee))
    assert reached.status_code == status.HTTP_200_OK, reached.text


@pytest.mark.asyncio
async def test_an_editor_cannot_invite_someone_as_an_owner(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    """The conferral bound on the invitation itself.

    Acceptance now honours the invited role, so the role an inviter may offer
    has to be bounded where it is chosen -- the same rule join-request approval
    already applies."""
    org_id = fixed_test_org["id"]

    editor = await signup_user()
    invite_editor = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": editor["email"], "role": "ORG_EDITOR"},
    )
    assert invite_editor.status_code == status.HTTP_201_CREATED, invite_editor.text
    accept = await async_client.post(
        f"/organizations/invitations/{invite_editor.json()['id']}/accept",
        headers=_headers(editor),
    )
    assert accept.status_code == status.HTTP_200_OK, accept.text

    outsider = await signup_user()
    overreach = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": outsider["email"], "role": "ORG_OWNER"},
        headers=_headers(editor),
    )
    assert overreach.status_code == status.HTTP_403_FORBIDDEN, overreach.text

    within_bounds = await async_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": outsider["email"], "role": "ORG_MEMBER"},
        headers=_headers(editor),
    )
    assert within_bounds.status_code == status.HTTP_201_CREATED, within_bounds.text
