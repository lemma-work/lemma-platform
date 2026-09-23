"""A delegated agent must not reach files through its principal's share links.

`ctx.user_id` on a delegated context is the *invoking person*, not the agent.
Scoping the share listing to that id alone therefore answered the wrong
question: it returned the person's links, including ones pointing at files the
agent itself holds no grant for — and each row carries the `code`, which is the
entire capability. An agent refused `pod_get_file_url` on an ungranted file
could list the person's existing share for it and fetch the bytes from `/s/`.

The rule being restored is PS-ACCESS-020: a workload gets the person's access
intersected with its own grants, never the union. Revocation is authorized the
same way, so a narrow agent cannot retire a link it could not have been shown.

Built on the delegation harness in `test_agent_workload_folder_grant_cascade_e2e`
— a real token from `build_delegation_claims`, not a stand-in context.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e

GRANTED = "/library"
UNGRANTED = "/vault"


async def _agent_client(test_app, *, user_id, agent_id, agent_name, pod_id):
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(agent_id),
        pod_id=UUID(pod_id),
        session_id=uuid4().hex,
        invoked_by_user_id=UUID(user_id),
        workload_name=agent_name,
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_a_narrow_agent_cannot_list_or_revoke_its_principals_other_shares(
    pod_api: DatastoreApi,
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    test_app,
):
    pod_id = str(pod_api.pod_id)

    granted_file = await pod_api.upload_file(
        "handbook.md", b"granted body", directory_path=GRANTED
    )
    ungranted_file = await pod_api.upload_file(
        "payroll.md", b"ungranted body", directory_path=UNGRANTED
    )

    # The person shares both. They may read both, so both are minted.
    codes = {}
    for entity in (granted_file, ungranted_file):
        resp = await pod_api.request(
            "POST",
            f"/pods/{pod_id}/datastore/files/signed-url",
            params={"path": entity["path"]},
            json={},
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        codes[entity["path"]] = resp.json()["signed_url"].rsplit("/", 1)[-1]

    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": f"narrow-{uuid4().hex[:6]}", "instruction": "Answer briefly."},
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text
    agent_body = agent.json()

    # Granted `/library` only. `/vault` is deliberately left out.
    perms = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_body['name']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent_body["name"],
                    "permission_ids": ["agent.read"],
                },
                {
                    "resource_type": "folder",
                    "resource_name": GRANTED,
                    "permission_ids": ["folder.read"],
                },
            ]
        },
    )
    assert perms.status_code == status.HTTP_200_OK, perms.text

    me = await authenticated_client.get("/users/me")
    assert me.status_code == status.HTTP_200_OK, me.text
    user_id = me.json()["id"]

    agent_client = await _agent_client(
        test_app,
        user_id=user_id,
        agent_id=agent_body["id"],
        agent_name=agent_body["name"],
        pod_id=pod_id,
    )
    async with agent_client:
        can_read = await agent_client.get(
            f"/pods/{pod_id}/datastore/files/by-path",
            params={"path": granted_file["path"]},
        )
        assert can_read.status_code == status.HTTP_200_OK, (
            "grant setup is wrong, not the listing: " + can_read.text
        )

        listed = await agent_client.get(
            f"/pods/{pod_id}/datastore/files/signed-urls",
            params={"include_dead": "true"},
        )
        assert listed.status_code == status.HTTP_200_OK, listed.text
        seen = {link["code"] for link in listed.json()["links"]}

        # The granted file's share is the agent's to see; the ungranted one is
        # the whole point — its code is a capability the agent cannot be handed.
        assert codes[granted_file["path"]] in seen, listed.json()
        assert codes[ungranted_file["path"]] not in seen, listed.json()

        # And it cannot retire what it cannot see.
        revoked = await agent_client.delete(
            f"/pods/{pod_id}/datastore/files/signed-urls/"
            f"{codes[ungranted_file['path']]}"
        )
        assert revoked.status_code == status.HTTP_200_OK, revoked.text
        assert revoked.json()["revoked"] is False

    # The person's own link is untouched and still works.
    still_live = await async_client.get(f"/s/{codes[ungranted_file['path']]}")
    assert still_live.status_code == status.HTTP_200_OK, still_live.text
