"""`GET /organizations/{org}/permissions/me`: what I may do in an organization.

The org-level twin of `/pods/{pod}/permissions/me`. A page asks for a
permission -- `org.installation.manage` -- never for a role, so the day a role's
holdings change no page has to. Before this the web app turned my role, read
off the navigation response, into permissions with a copy of the server's
`SYSTEM_ROLE_PERMISSIONS`; a copy is a second answer, and two answers drift.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from starlette import status

pytestmark = pytest.mark.e2e

_ORG_PERMISSIONS = {
    "org.read",
    "org.update",
    "org.member.manage",
    "org.invitation.manage",
    "org.billing.manage",
    "org.installation.manage",
    "pod.create",
}


def _headers(user: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


async def _join(
    owner: AsyncClient, anon: AsyncClient, org_id: str, user: dict, role: str
) -> None:
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


async def _mine(anon: AsyncClient, org_id: str, user: dict) -> set[str]:
    response = await anon.get(
        f"/organizations/{org_id}/permissions/me", headers=_headers(user)
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["organization_id"] == org_id
    assert body["actions"] == sorted(body["actions"]), "a stable order"
    return set(body["actions"])


@pytest.mark.asyncio
async def test_each_role_reads_exactly_its_own_org_permissions(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    signup_user,
):
    org_id = fixed_test_org["id"]
    editor, member, outsider = (
        await signup_user(),
        await signup_user(),
        await signup_user(),
    )
    await _join(authenticated_client, async_client, org_id, editor, "ORG_EDITOR")
    await _join(authenticated_client, async_client, org_id, member, "ORG_MEMBER")

    owner = await authenticated_client.get(f"/organizations/{org_id}/permissions/me")
    assert owner.status_code == status.HTTP_200_OK, owner.text
    assert set(owner.json()["actions"]) == _ORG_PERMISSIONS

    edits = await _mine(async_client, org_id, editor)
    assert "org.installation.manage" in edits
    assert "org.billing.manage" not in edits, "billing is the owner's"
    assert edits == _ORG_PERMISSIONS - {"org.billing.manage"}

    assert await _mine(async_client, org_id, member) == {"org.read"}
    # Somebody outside it holds nothing there, and is told nothing more.
    assert await _mine(async_client, org_id, outsider) == set()


@pytest.mark.asyncio
async def test_only_org_level_permissions_are_listed(
    authenticated_client: AsyncClient, fixed_test_org
):
    """An owner administers every pod, but pod permissions are the pod
    route's answer, asked of a pod; this one lists the organization's."""
    response = await authenticated_client.get(
        f"/organizations/{fixed_test_org['id']}/permissions/me"
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    assert not {
        action for action in response.json()["actions"] if action.startswith("pod.")
    } - {"pod.create"}
