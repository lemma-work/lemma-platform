from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.infrastructure.events import stream_observability as observation


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.records.append((event, fields))


@pytest.mark.asyncio
async def test_snapshot_accepts_normalized_last_delivered_id(monkeypatch) -> None:
    client = AsyncMock()
    client.exists.return_value = 1
    client.xinfo_stream.return_value = {
        "length": 120,
        "last-generated-id": b"1000000-0",
    }
    client.xinfo_groups.return_value = [
        {
            "name": b"workers",
            "consumers": 2,
            "pending": 1,
            "last-delivered-id": b"900000-0",
            "lag": 42,
        }
    ]
    client.xpending_range.return_value = [
        {"message_id": b"800000-0", "time_since_delivered": 1_234}
    ]
    client.xinfo_consumers.return_value = [
        {"name": b"active", "idle": 100},
        {"name": b"stale", "idle": 1_000_000},
    ]
    client.memory_usage.return_value = 4_096
    logger = _Logger()
    monkeypatch.setattr(observation, "logger", logger)
    monkeypatch.setattr(observation.time, "time", lambda: 1_000.0)

    await observation._snapshot_stream(client, "function_run_events")

    assert logger.records == [
        (
            "redis.stream.snapshot",
            {
                "stream": "function_run_events",
                "group": "workers",
                "length": 120,
                "delayed": 0,
                "caught_up": False,
                "pending": 1,
                "reported_lag": 42,
                "last_delivered_age_seconds": 100,
                "oldest_pending_ms": 1_234,
                "consumers": 2,
                "active_consumers": 1,
                "memory_bytes": 4_096,
                "maxlen": 50_000,
            },
        )
    ]


def test_streaq_queue_is_observed_but_not_managed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "worker_queue_name", "priority")

    streams = observation.observable_streams()

    assert "streaq:priority:queues:normal" in streams


@pytest.mark.asyncio
async def test_a_caught_up_group_is_silent_however_long_the_stream_is(
    monkeypatch,
) -> None:
    """Retained history is not a symptom.

    A stream keeps its entries up to maxlen, so `length` is non-zero for the
    rest of the life of any stream that has ever been written to. While it
    counted as "worth reporting", every healthy group reported on every cycle:
    on the deployment where this was found those records were nearly all of the
    worker's output, and the errors underneath them were unreadable.
    """
    client = AsyncMock()
    client.exists.return_value = 1
    client.xinfo_stream.return_value = {
        "length": 9_187,
        "last-generated-id": b"1000000-0",
    }
    client.xinfo_groups.return_value = [
        {
            "name": b"agent-events",
            "consumers": 2,
            "pending": 0,
            "last-delivered-id": b"1000000-0",
            "lag": 0,
        }
    ]
    client.xinfo_consumers.return_value = [{"name": b"active", "idle": 100}]
    client.memory_usage.return_value = 4_096
    logger = _Logger()
    monkeypatch.setattr(observation, "logger", logger)
    monkeypatch.setattr(observation.time, "time", lambda: 1_000.0)

    reported = await observation._snapshot_stream(client, "agent_events")

    assert reported == 0
    assert logger.records == []


@pytest.mark.asyncio
async def test_entries_with_nothing_reading_them_still_report(monkeypatch) -> None:
    """The one place length is the whole signal: no group means no reader."""
    client = AsyncMock()
    client.exists.return_value = 1
    client.xinfo_stream.return_value = {
        "length": 9_187,
        "last-generated-id": b"1000000-0",
    }
    client.xinfo_groups.return_value = []
    client.memory_usage.return_value = 4_096
    logger = _Logger()
    monkeypatch.setattr(observation, "logger", logger)
    monkeypatch.setattr(observation.time, "time", lambda: 1_000.0)

    reported = await observation._snapshot_stream(client, "agent_events")

    assert reported == 1
    assert [event for event, _ in logger.records] == ["redis.stream.snapshot"]
    assert logger.records[0][1]["length"] == 9_187
