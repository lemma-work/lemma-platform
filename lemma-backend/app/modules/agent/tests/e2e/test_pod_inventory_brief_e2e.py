"""A table created a moment ago is in the next run's brief.

The rendered brief is cached for a minute, and an agent that had just created a
table went on reading a brief without it until the entry expired. A committed
write to anything the brief lists replaces the pod's inventory token in Redis
(``core/infrastructure/db/pod_inventory``), and the cache is keyed by it. This
runs the real write path, the real token and the real cache.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import status

from app.core.infrastructure.db import pod_inventory
from app.modules.agent.services.context_brief import agent_brief as brief_mod
from app.modules.agent.tests.e2e.test_agent_self_brief_e2e import (
    _create_pod,
    _default_agent_brief,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_a_table_created_reaches_the_next_brief_inside_the_cache_ttl(
    authenticated_client, fixed_test_org, fixed_test_user, monkeypatch
):
    monkeypatch.setattr(
        brief_mod.agent_settings, "agent_context_brief_cache_ttl_seconds", 600
    )
    pod = await _create_pod(authenticated_client, fixed_test_org, "inventory-token")
    await pod_inventory.settle()
    before = await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])
    token = await pod_inventory.pod_inventory_generation(UUID(pod["id"]))
    assert "board_games" not in before

    created = await authenticated_client.post(
        f"/pods/{pod['id']}/datastore/tables",
        json={
            "table_name": "board_games",
            "primary_key_column": "id",
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
        },
    )
    assert created.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    await pod_inventory.settle()

    assert await pod_inventory.pod_inventory_generation(UUID(pod["id"])) != token
    after = await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])
    assert "board_games" in after
    # Unchanged since: served from the cache, not rebuilt.
    assert (
        await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])
        == after
    )
