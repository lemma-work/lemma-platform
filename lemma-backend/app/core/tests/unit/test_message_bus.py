from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.infrastructure.events import message_bus
from app.core.infrastructure.events.config import event_transport_settings


@pytest.mark.asyncio
async def test_partially_connected_broker_is_stopped(monkeypatch):
    stopped = False

    class _FailingBroker:
        def __init__(self, redis_url: str, **kwargs) -> None:
            assert redis_url == "redis://message-bus-test"
            assert kwargs["logger"].name == "faststream.redis"

        async def connect(self) -> None:
            raise ConnectionError("redis unavailable")

        async def stop(self) -> None:
            nonlocal stopped
            stopped = True

    monkeypatch.setattr(message_bus, "RedisBroker", _FailingBroker)
    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await bus.connect()

    assert stopped
    assert bus._broker is None


@pytest.mark.asyncio
async def test_publish_ensures_declared_groups_before_stream_write(monkeypatch):
    order: list[str] = []

    class _Broker:
        def __init__(self, redis_url: str, **kwargs) -> None:
            assert redis_url == "redis://message-bus-test"
            assert kwargs["logger"].name == "faststream.redis"
            self._connection = AsyncMock()
            self._connection.xinfo_groups.return_value = []

        async def connect(self) -> None:
            return None

        async def publish(self, payload, *, stream: str, maxlen: int | None) -> None:
            assert payload == {"event_type": "test.created"}
            assert stream == "test_events"
            assert maxlen == 50_000
            order.append("publish")

        async def stop(self) -> None:
            return None

    async def ensure(redis_client, stream: str) -> None:
        assert redis_client is not None
        assert stream == "test_events"
        order.append("ensure")

    monkeypatch.setattr(message_bus, "RedisBroker", _Broker)
    monkeypatch.setattr(message_bus, "ensure_stream_groups", ensure)
    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    await bus.publish("test_events", {"event_type": "test.created"})

    assert order == ["ensure", "publish"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("groups", "stream_last_id", "expected"),
    [
        ([], "100-0", None),
        (
            [{"name": "workers", "pending": 1, "lag": 0, "last-delivered-id": "100-0"}],
            "100-0",
            None,
        ),
        (
            [{"name": "workers", "pending": 0, "lag": 60, "last-delivered-id": "40-0"}],
            "100-0",
            None,
        ),
        (
            # Redis may report historical lag for a group created at `$`.
            [{"name": "workers", "pending": 0, "lag": 60, "last-delivered-id": "100-0"}],
            "100-0",
            100,
        ),
        (
            [{"name": "workers", "pending": 0, "lag": 10, "last-delivered-id": "90-0"}],
            "100-0",
            100,
        ),
        (
            [
                {
                    "name": "workers",
                    "pending": 0,
                    "lag": 0,
                    "last-delivered-id": "100-0",
                },
                {
                    "name": "obsolete",
                    "pending": 1,
                    "lag": 0,
                    "last-delivered-id": "20-0",
                },
            ],
            "100-0",
            None,
        ),
    ],
)
async def test_grouped_stream_maxlen_fails_safe_and_handles_phantom_lag(
    monkeypatch,
    groups,
    stream_last_id,
    expected,
) -> None:
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 100)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})
    monkeypatch.setattr(
        message_bus,
        "registered_groups_for_stream",
        lambda _stream: {"workers"},
    )
    client = AsyncMock()
    client.xinfo_groups.return_value = groups
    client.xinfo_stream.return_value = {"last-generated-id": stream_last_id}

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(client, "events") == expected


@pytest.mark.asyncio
async def test_undeclared_observed_group_still_blocks_trimming(monkeypatch) -> None:
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 100)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})
    monkeypatch.setattr(
        message_bus,
        "registered_groups_for_stream",
        lambda _stream: set(),
    )
    client = AsyncMock()
    client.xinfo_groups.return_value = [
        {
            "name": "obsolete",
            "pending": 1,
            "lag": 0,
            "last-delivered-id": "20-0",
        }
    ]
    client.xinfo_stream.return_value = {"last-generated-id": "100-0"}

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(client, "events") is None
