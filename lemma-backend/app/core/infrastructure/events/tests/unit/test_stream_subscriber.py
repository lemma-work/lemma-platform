from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.infrastructure.events import stream_subscriber as ss


def test_redis_stream_sub_registers_grouped_streams(monkeypatch):
    monkeypatch.setattr(ss, "_REGISTERED_STREAM_GROUPS", set())

    ss.redis_stream_sub("stream_a", group="group_a", consumer="c1")
    ss.redis_stream_sub("stream_b", group="group_b", consumer="c2")
    # Ungrouped subscribers use plain XREAD — no consumer group to reconcile.
    ss.redis_stream_sub("stream_c")

    assert ss.registered_stream_groups() == {
        ("stream_a", "group_a"),
        ("stream_b", "group_b"),
    }


@pytest.mark.asyncio
async def test_ensure_consumer_groups_creates_each_group(monkeypatch):
    monkeypatch.setattr(
        ss,
        "_REGISTERED_STREAM_GROUPS",
        {("agent_events", "agent-events"), ("schedule_events", "wf-sched")},
    )
    client = AsyncMock()
    client.xgroup_create = AsyncMock()

    created = await ss.ensure_consumer_groups(client)

    assert created == 2
    assert client.xgroup_create.await_count == 2
    # Always created with mkstream + at the stream end ($).
    for call in client.xgroup_create.await_args_list:
        assert call.kwargs["mkstream"] is True
        assert call.kwargs["id"] == "$"


@pytest.mark.asyncio
async def test_ensure_consumer_groups_ignores_existing_group(monkeypatch):
    monkeypatch.setattr(
        ss, "_REGISTERED_STREAM_GROUPS", {("agent_events", "agent-events")}
    )
    client = AsyncMock()
    client.xgroup_create = AsyncMock(
        side_effect=Exception("BUSYGROUP Consumer Group name already exists")
    )

    # Existing group is the happy path: no raise, nothing counted as created.
    created = await ss.ensure_consumer_groups(client)

    assert created == 0


@pytest.mark.asyncio
async def test_ensure_consumer_groups_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        ss, "_REGISTERED_STREAM_GROUPS", {("agent_events", "agent-events")}
    )
    client = AsyncMock()
    client.xgroup_create = AsyncMock(side_effect=Exception("connection refused"))

    # Must never raise — group plumbing cannot crash the worker.
    created = await ss.ensure_consumer_groups(client)

    assert created == 0
