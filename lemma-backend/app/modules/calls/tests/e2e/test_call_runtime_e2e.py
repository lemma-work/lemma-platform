"""A call is refused up front when its teammate runs where the caller cannot.

On dev a pod's teammate ran on another member's own Claude Code. The call
opened, the voice talked, and every request behind it was refused -- so the
person was asked to repeat themselves for the whole call. Opening the call now
runs the turn-start check: refused with the reason a turn would give, and no
call conversation left behind.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.tests.e2e.test_retired_model_e2e import (
    _make_personal,
    _provider,
)
from app.modules.calls.config import calls_settings
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_a_call_on_a_teammate_someone_else_runs_is_refused_before_it_opens(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calls_settings, "gemini_api_key", "test-key-unused")
    org_id = fixed_test_org["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    async def _editor(slug: str) -> tuple[dict, dict[str, str]]:
        user = await signup_user(async_client, slug)
        member = await invite_org_member(
            authenticated_client, async_client, org_id=org_id, user=user
        )
        await add_pod_member(
            authenticated_client,
            pod_id=pod_id,
            organization_member_id=member["id"],
            role="POD_EDITOR",
        )
        return user, auth_headers(user)

    owner, as_owner = await _editor(f"call-owner-{uuid4().hex[:6]}")
    _peer, as_peer = await _editor(f"call-peer-{uuid4().hex[:6]}")
    provider = await _provider(authenticated_client, org_id, model_names=["kept"])
    await _make_personal(provider["id"], owner_id=owner["id"], first_name="Deepak")
    agent_name = f"factory-{uuid4().hex[:6]}"
    saved = await async_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Answer.",
            "visibility": "POD",
            "agent_runtime": {"profile_id": provider["id"], "model_name": "kept"},
        },
        headers=as_owner,
    )
    assert saved.status_code == status.HTTP_201_CREATED, saved.text
    call = {"agent_name": agent_name, "display_name": "Factory"}

    # Its owner runs it, so their call opens.
    mine = await async_client.post(f"/pods/{pod_id}/calls", json=call, headers=as_owner)
    assert mine.status_code == status.HTTP_201_CREATED, mine.text

    # Anybody else is told why, before a voice is opened.
    theirs = await async_client.post(
        f"/pods/{pod_id}/calls", json=call, headers=as_peer
    )
    assert theirs.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, theirs.text
    assert theirs.json()["code"] == "runtime_profile_not_shareable"
    assert "Deepak" in theirs.json()["message"]
    listed = await async_client.get(f"/pods/{pod_id}/conversations", headers=as_peer)
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert not [
        each
        for each in listed.json()["items"]
        if (each.get("metadata") or {}).get("call")
    ]
