"""A byte budget that cannot destroy unread work.

The count cap could not react when payloads grew: bytes moved 5.5x while the
length stayed pinned at 50,000. This is the backstop for that, and its hard
constraint is that reclaiming memory must never cost a consumer its entries.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.infrastructure.events import stream_budget
from app.core.infrastructure.events.config import event_transport_settings


@pytest.mark.asyncio
async def test_a_stream_within_budget_is_left_alone(monkeypatch):
    monkeypatch.setattr(event_transport_settings, "redis_stream_max_bytes", 1_000)
    client = AsyncMock()
    client.memory_usage.return_value = 500

    assert await stream_budget.trim_streams_to_budget(client, streams={"events"}) == {}
    client.xtrim.assert_not_called()


@pytest.mark.asyncio
async def test_it_trims_only_to_the_consumed_watermark(monkeypatch):
    """MINID at the *oldest* delivered id, so the slowest group keeps its work."""
    monkeypatch.setattr(event_transport_settings, "redis_stream_max_bytes", 1_000)
    client = AsyncMock()
    client.memory_usage.side_effect = [5_000, 400]
    client.xinfo_groups.return_value = [
        {"name": "fast", "last-delivered-id": "900-0"},
        {"name": "slow", "last-delivered-id": "100-5"},
    ]

    reclaimed = await stream_budget.trim_streams_to_budget(client, streams={"events"})

    client.xtrim.assert_awaited_once_with("events", minid="100-5", approximate=True)
    assert reclaimed == {"events": 4_600}


@pytest.mark.asyncio
async def test_unreadable_consumer_progress_trims_nothing(monkeypatch):
    monkeypatch.setattr(event_transport_settings, "redis_stream_max_bytes", 1_000)
    client = AsyncMock()
    client.memory_usage.return_value = 5_000
    client.xinfo_groups.return_value = [{"name": "workers", "last-delivered-id": None}]

    assert await stream_budget.trim_streams_to_budget(client, streams={"events"}) == {}
    client.xtrim.assert_not_called()


@pytest.mark.asyncio
async def test_a_stream_nobody_reads_can_be_trimmed_whole(monkeypatch):
    """No consumer group means no unread work by definition."""
    monkeypatch.setattr(event_transport_settings, "redis_stream_max_bytes", 1_000)
    client = AsyncMock()
    client.memory_usage.side_effect = [5_000, 0]
    client.xinfo_groups.return_value = []

    await stream_budget.trim_streams_to_budget(client, streams={"events"})

    client.xtrim.assert_awaited_once_with("events", minid="+", approximate=True)


@pytest.mark.asyncio
async def test_unread_entries_over_budget_are_reported_not_removed(monkeypatch):
    monkeypatch.setattr(event_transport_settings, "redis_stream_max_bytes", 1_000)
    client = AsyncMock()
    # Still over budget after trimming everything that was consumed.
    client.memory_usage.side_effect = [5_000, 4_000]
    client.xinfo_groups.return_value = [
        {"name": "workers", "last-delivered-id": "10-0"}
    ]

    reclaimed = await stream_budget.trim_streams_to_budget(client, streams={"events"})

    assert reclaimed == {"events": 1_000}


@pytest.mark.asyncio
async def test_zero_disables_the_backstop(monkeypatch):
    monkeypatch.setattr(event_transport_settings, "redis_stream_max_bytes", 0)
    client = AsyncMock()

    assert await stream_budget.trim_streams_to_budget(client, streams={"events"}) == {}
    client.memory_usage.assert_not_called()
