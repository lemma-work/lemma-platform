from __future__ import annotations

import json

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
    def __init__(self, pubsub_instances: list[_FakePubSub] | None = None) -> None:
        self.pubsub_instances = pubsub_instances or [_FakePubSub()]
        self.pubsub_instance = self.pubsub_instances[0]
        self.pubsub_calls = 0
        self.published: list[tuple[str, str | bytes]] = []
        self.closed = False

    def pubsub(self, *, ignore_subscribe_messages: bool):
        assert ignore_subscribe_messages is True
        instance = self.pubsub_instances[self.pubsub_calls]
        self.pubsub_calls += 1
        return instance

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


class _FailsOnSubscribePubSub(_FakePubSub):
    def __init__(self, error: BaseException, *, after: int = 0) -> None:
        super().__init__()
        self.error = error
        self.after = after
        self.subscribe_calls: list[tuple[str, ...]] = []

    async def subscribe(self, *channels: str) -> None:
        self.subscribe_calls.append(channels)
        if len(self.subscribe_calls) > self.after:
            raise self.error
        await super().subscribe(*channels)


@pytest.mark.asyncio
async def test_realtime_adapter_replaces_stale_pubsub_on_subscribe() -> None:
    stale = _FailsOnSubscribePubSub(RedisConnectionError("stale"))
    replacement = _FakePubSub()
    redis = _FakeRedis([stale, replacement])
    adapter = RedisChannelAdapter(client=redis)  # type: ignore[arg-type]

    async with adapter.subscribe(["conversation:1"]):
        assert stale.closed is True
        assert replacement.subscribed == ("conversation:1",)

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_realtime_adapter_resubscribes_existing_channels_after_stale_subscribe() -> (
    None
):
    stale = _FailsOnSubscribePubSub(
        RedisConnectionError("stale"),
        after=1,
    )
    replacement = _FakePubSub()
    redis = _FakeRedis([stale, replacement])
    adapter = RedisChannelAdapter(client=redis)  # type: ignore[arg-type]

    async with adapter.subscribe(["conversation:1"]):
        async with adapter.subscribe(["conversation:2"]):
            assert stale.closed is True
            assert replacement.subscribed == (
                "conversation:1",
                "conversation:2",
            )

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_realtime_adapter_propagates_second_subscribe_failure() -> None:
    stale = _FailsOnSubscribePubSub(RedisConnectionError("stale"))
    unavailable = _FailsOnSubscribePubSub(RedisTimeoutError("still unavailable"))
    redis = _FakeRedis([stale, unavailable])
    adapter = RedisChannelAdapter(client=redis)  # type: ignore[arg-type]

    with pytest.raises(RedisTimeoutError, match="still unavailable"):
        async with adapter.subscribe(["conversation:1"]):
            pass

    assert stale.closed is True
    assert unavailable.closed is False
    await adapter.disconnect()


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
