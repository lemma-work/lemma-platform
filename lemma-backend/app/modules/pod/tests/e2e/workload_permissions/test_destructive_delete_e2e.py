"""Destructive-action gating for delegated workloads, end-to-end.

Deleting an agent is destructive (`agent.delete`). No workload — named or the
default pod agent — may delete one by default; it needs an explicit grant
(standing authority) or a session approval. This exercises the shared
``require_resource_admin_or_creator`` dependency chokepoint (which had a
creator shortcut that a workload delegating for the creator could otherwise
use to bypass the gate).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.pod.tests.e2e.workload_permissions.harness import (
    AGENT,
    create_agent,
    create_pod,
    create_workload,
    mint_default_pod_agent_client,
    mint_workload_client,
    replace_workload_grants,
)

pytestmark = pytest.mark.e2e


def _agent_delete_grant(name: str) -> dict:
    return {
        "resource_type": "agent",
        "resource_name": name,
        "permission_ids": ["agent.delete"],
    }


async def _delete_agent(client, pod_id: str, agent_name: str):
    return await client.request("DELETE", f"/pods/{pod_id}/agents/{agent_name}")


@pytest.mark.asyncio
async def test_named_workload_agent_delete_requires_grant_or_approval(
    test_app, authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]
    victim = await create_agent(authenticated_client, pod_id, f"victim_{suffix}")

    name = f"deleter_{suffix}"
    workload = await create_workload(authenticated_client, pod_id, AGENT, name)
    # No agent.delete grant.
    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    try:
        denied = await _delete_agent(client, pod_id, victim["name"])
        assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text
    finally:
        await client.aclose()

    # Grant agent.delete explicitly -> standing authority.
    await replace_workload_grants(
        authenticated_client, pod_id, AGENT, name, [_agent_delete_grant(victim["name"])]
    )
    granted = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    try:
        ok = await _delete_agent(granted, pod_id, victim["name"])
        assert ok.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT), ok.text
    finally:
        await granted.aclose()


@pytest.mark.asyncio
async def test_default_pod_agent_agent_delete_is_gated_despite_user_admin(
    test_app, authenticated_client, fixed_test_org, fixed_test_user
):
    """The default pod agent mirrors the invoking (admin) user, but destructive
    actions are the carve-out: deleting an agent is gated without approval, even
    though the user could delete it directly. This covers the shared dependency
    creator shortcut (the agent's creator is the invoking user)."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]
    victim = await create_agent(authenticated_client, pod_id, f"victim_{suffix}")

    client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    try:
        denied = await _delete_agent(client, pod_id, victim["name"])
        assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text
    finally:
        await client.aclose()

    # The human user (not delegated) can still delete it directly.
    human = await _delete_agent(authenticated_client, pod_id, victim["name"])
    assert human.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT), human.text


@pytest.mark.asyncio
async def test_default_pod_agent_cannot_delete_pod(
    test_app, authenticated_client, fixed_test_org, fixed_test_user
):
    """pod.delete is destructive: even the default pod agent (mirroring an admin
    user) is gated. The DELETE /pods/{id} route authorizes via
    require_action(POD_DELETE) -> the gate."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    try:
        denied = await client.request("DELETE", f"/pods/{pod_id}")
        assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text
    finally:
        await client.aclose()
    # The pod still exists — the human admin can delete it.
    human = await authenticated_client.request("DELETE", f"/pods/{pod_id}")
    assert human.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT), human.text


@pytest.mark.asyncio
async def test_default_pod_agent_cannot_manage_members(
    test_app, authenticated_client, fixed_test_org, fixed_test_user
):
    """pod.member.manage is destructive: the default pod agent is gated at the
    require_action(POD_MEMBER_MANAGE) dependency, before the endpoint body."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    try:
        # The gate fires before the (missing) member is resolved -> 403, not 404.
        removed = await client.request(
            "DELETE", f"/pods/{pod_id}/members/{uuid4()}"
        )
        assert removed.status_code == status.HTTP_403_FORBIDDEN, removed.text
        updated = await client.request(
            "PATCH",
            f"/pods/{pod_id}/members/{uuid4()}/roles",
            json={"roles": ["POD_VIEWER"]},
        )
        assert updated.status_code == status.HTTP_403_FORBIDDEN, updated.text
    finally:
        await client.aclose()
