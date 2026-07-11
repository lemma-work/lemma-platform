from __future__ import annotations

import json

import pytest

from app.core.infrastructure.channels import channel_service as channel_module
from app.core.infrastructure.channels.channel_service import RedisChannelAdapter


class _FakePubSub:
    def __init__(self) -> None:
        self.subscribed: tuple[str, ...] = ()
        self.unsubscribed: tuple[str, ...] = ()
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed = channels

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed = channels

    async def listen(self):
        yield {"type": "subscribe", "data": None}
        yield {"type": "message", "data": '{"type":"token"}'}

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self) -> None:
        self.pubsub_instance = _FakePubSub()
        self.published: list[tuple[str, str | bytes]] = []
        self.closed = False

    def pubsub(self, *, ignore_subscribe_messages: bool):
        assert ignore_subscribe_messages is True
        return self.pubsub_instance

    async def publish(self, channel: str, payload: str | bytes) -> None:
        self.published.append((channel, payload))

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_realtime_adapter_publishes_json_and_releases_subscription() -> None:
    redis = _FakeRedis()
    adapter = RedisChannelAdapter(client=redis)  # type: ignore[arg-type]

    await adapter.publish("conversation:1", {"type": "token", "data": "hi"})
    assert redis.published[0][0] == "conversation:1"
    assert json.loads(redis.published[0][1]) == {"type": "token", "data": "hi"}

    async with adapter.subscribe(["conversation:1"]) as messages:
        assert await anext(messages) == '{"type":"token"}'

    assert redis.pubsub_instance.subscribed == ("conversation:1",)
    assert redis.pubsub_instance.unsubscribed == ("conversation:1",)
    # The one process-wide Pub/Sub lease stays open for later subscribers.
    assert redis.pubsub_instance.closed is False
    await adapter.disconnect()
    assert redis.pubsub_instance.closed is True


@pytest.mark.asyncio
async def test_realtime_adapter_closes_owned_pool(monkeypatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(channel_module.Redis, "from_url", lambda *args, **kwargs: redis)
    adapter = RedisChannelAdapter("redis://test")

    await adapter.connect()
    await adapter.disconnect()

    assert redis.closed is True


@pytest.mark.asyncio
async def test_realtime_adapter_rejects_empty_subscription() -> None:
    adapter = RedisChannelAdapter(client=_FakeRedis())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="At least one"):
        async with adapter.subscribe([]):
            pass


@pytest.mark.asyncio
async def test_realtime_adapter_ref_counts_shared_channel() -> None:
    redis = _FakeRedis()
    adapter = RedisChannelAdapter(client=redis)  # type: ignore[arg-type]

    async with adapter.subscribe(["conversation:1"]):
        async with adapter.subscribe(["conversation:1"]):
            assert redis.pubsub_instance.subscribed == ("conversation:1",)
        assert redis.pubsub_instance.unsubscribed == ()

    assert redis.pubsub_instance.unsubscribed == ("conversation:1",)
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_realtime_adapter_evicts_only_slow_client() -> None:
    redis = _FakeRedis()
    adapter = RedisChannelAdapter(client=redis)  # type: ignore[arg-type]

    async with adapter.subscribe(["slow"]) as slow_messages:
        async with adapter.subscribe(["fast"]) as fast_messages:
            for index in range(257):
                await adapter._fan_out("slow", str(index))
            await adapter._fan_out("fast", "still-connected")

            with pytest.raises(RuntimeError, match="fell behind"):
                await anext(slow_messages)
            assert await anext(fast_messages) == "still-connected"

    await adapter.disconnect()
