"""Changing a surface needs rights on the agent it answers as.

A pod editor holds AGENT_UPDATE pod-wide, but a RESTRICTED agent is closed to
them without a grant. The surface PATCH used to ask only about the agent named
in the request -- and a request naming none was asked about the pod's own
assistant, which every editor may change -- so an editor could switch off,
rewire or take over a restricted agent's surface.
"""

from __future__ import annotations

from app.modules.agent_surfaces.tests.e2e.helpers import share_account_as_connector

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.helpers import _ensure_connector_account
from app.modules.test_support.e2e_authz import create_role_visibility_context

pytestmark = pytest.mark.e2e


async def _restricted_agent_surface(
    owner: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    owner_user_id: str,
    slack_base_url: str,
) -> tuple[str, str]:
    agent_name = f"restricted_{uuid4().hex[:8]}"
    created_agent = await owner.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Answer briefly.",
            "visibility": "RESTRICTED",
        },
    )
    assert created_agent.status_code == status.HTTP_201_CREATED, created_agent.text
    account = await _ensure_connector_account(
        db_session,
        user_id=owner_user_id,
        connector_id="slack",
        credentials={
            "access_token": f"xoxb-{uuid4().hex[:8]}",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": slack_base_url,
            "raw_response": {
                "bot_user_id": "U0AUTHZ",
                "team_id": f"T{uuid4().hex[:8].upper()}",
                "api_base_url": slack_base_url,
            },
        },
    )
    created = await owner.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "default_agent_name": agent_name,
            "pod_connector": await share_account_as_connector(
                owner, pod_id, account.id
            ),
        },
    )
    assert created.status_code == status.HTTP_200_OK, created.text
    return agent_name, created.json()["name"]


async def test_an_editor_without_rights_on_the_agent_cannot_change_its_surface(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_org,
    fixed_test_user,
    fake_slack,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="surface-update-authz",
    )
    pod_id = ctx["pod_id"]
    agent_name, surface_name = await _restricted_agent_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        owner_user_id=fixed_test_user["id"],
        slack_base_url=fake_slack.base_url,
    )
    surface_url = f"/pods/{pod_id}/surfaces/{surface_name}"
    before = (await authenticated_client.get(surface_url)).json()

    disabled = await async_client.patch(
        surface_url, json={"is_enabled": False}, headers=ctx["editor_headers"]
    )
    moved_to_the_assistant = await async_client.patch(
        surface_url, json={"default_agent_name": None}, headers=ctx["editor_headers"]
    )

    assert disabled.status_code == status.HTTP_403_FORBIDDEN, disabled.text
    assert moved_to_the_assistant.status_code == status.HTTP_403_FORBIDDEN, (
        moved_to_the_assistant.text
    )
    unchanged = await authenticated_client.get(surface_url)
    assert unchanged.status_code == status.HTTP_200_OK, unchanged.text
    assert unchanged.json()["status"] == before["status"]
    assert unchanged.json()["agent_name"] == agent_name


async def test_moving_a_surface_onto_a_restricted_agent_needs_rights_on_it(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_org,
    fixed_test_user,
    fake_slack,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="surface-move-authz",
    )
    pod_id = ctx["pod_id"]
    agent_name, surface_name = await _restricted_agent_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        owner_user_id=fixed_test_user["id"],
        slack_base_url=fake_slack.base_url,
    )
    surface_url = f"/pods/{pod_id}/surfaces/{surface_name}"
    handed_back = await authenticated_client.patch(
        surface_url, json={"default_agent_name": None}
    )
    assert handed_back.status_code == status.HTTP_200_OK, handed_back.text

    taken = await async_client.patch(
        surface_url,
        json={"default_agent_name": agent_name},
        headers=ctx["editor_headers"],
    )
    disabled_on_the_assistant = await async_client.patch(
        surface_url, json={"is_enabled": False}, headers=ctx["editor_headers"]
    )

    assert taken.status_code == status.HTTP_403_FORBIDDEN, taken.text
    # The pod's own assistant is every editor's to change.
    assert disabled_on_the_assistant.status_code == status.HTTP_200_OK, (
        disabled_on_the_assistant.text
    )
