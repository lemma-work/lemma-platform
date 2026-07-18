from __future__ import annotations

import pytest

from app.core.infrastructure.events import message_bus


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
            self._connection = object()

        async def connect(self) -> None:
            return None

        async def publish(self, payload, *, stream: str) -> None:
            assert payload == {"event_type": "test.created"}
            assert stream == "test_events"
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
