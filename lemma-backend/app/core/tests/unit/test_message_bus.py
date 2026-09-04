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
        ([], "100-0", 400),
        (
            [{"name": "workers", "pending": 1, "lag": 0, "last-delivered-id": "100-0"}],
            "100-0",
            400,
        ),
        (
            [{"name": "workers", "pending": 0, "lag": 60, "last-delivered-id": "40-0"}],
            "100-0",
            400,
        ),
        (
            # Redis may report historical lag for a group created at `$`.
            [
                {
                    "name": "workers",
                    "pending": 0,
                    "lag": 60,
                    "last-delivered-id": "100-0",
                }
            ],
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
            400,
        ),
    ],
)
async def test_grouped_stream_relaxes_to_hard_ceiling_and_handles_phantom_lag(
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
async def test_undeclared_observed_group_relaxes_but_still_caps(monkeypatch) -> None:
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

    # Never `None`: an obsolete group protecting one unacked message used to
    # switch trimming off entirely, and the stream grew until Redis died.
    assert await bus._safe_publish_maxlen(client, "events") == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "groups",
    [
        [],
        [{"name": "workers", "pending": 1, "lag": 0, "last-delivered-id": "1-0"}],
        [{"name": "workers", "pending": 0, "lag": 10_000, "last-delivered-id": "1-0"}],
        [{"name": "ghost", "pending": 99, "lag": 99, "last-delivered-id": "1-0"}],
        [{"name": "workers", "pending": None, "lag": None, "last-delivered-id": None}],
    ],
)
async def test_trimming_is_never_switched_off_while_it_is_enabled(
    monkeypatch, groups
) -> None:
    """No consumer state may produce an uncapped publish.

    This is the invariant the outage broke. `_safe_publish_maxlen` returned
    `None` -- XADD with no MAXLEN -- whenever a group looked behind, so a single
    unacked message in one of four groups switched trimming off for
    `datastore.events`. The stream then tracked `50000 + lag` upward for hours
    until Redis exceeded its container limit and login went down.

    A backlog may raise the ceiling. Nothing may remove it.
    """
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 100)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})
    client = AsyncMock()
    client.xinfo_groups.return_value = groups
    client.xinfo_stream.return_value = {"last-generated-id": "100-0"}

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(client, "events") is not None


@pytest.mark.asyncio
async def test_unreadable_group_state_on_an_existing_stream_still_caps(
    monkeypatch,
) -> None:
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 100)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})
    client = AsyncMock()
    client.xinfo_groups.side_effect = RuntimeError("no group state")
    client.exists.return_value = True

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(client, "events") == 400


@pytest.mark.asyncio
async def test_trimming_disabled_by_config_stays_disabled(monkeypatch) -> None:
    """`maxlen = 0` is the one place "no cap" is a choice, not an accident."""
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 0)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(AsyncMock(), "events") is None


@pytest.mark.asyncio
async def test_an_entry_stuck_past_the_hold_window_stops_blocking_the_cap(
    monkeypatch,
) -> None:
    """The production fault, in one test.

    One `datastore.file.created` message went unacked in one of four groups. It
    had two deliveries -- far short of quarantine's twelve -- so nothing ever
    took it away, and `pending != 0` disabled trimming for the whole stream.
    The stream grew past its cap for hours and no signal said so.

    An entry idle beyond the reclaim window has already failed its consumer and
    the reclaimer. It stops being a reason to retain everything behind it.
    """
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 100)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})
    monkeypatch.setattr(
        event_transport_settings, "redis_stream_pending_hold_seconds", 900
    )
    client = AsyncMock()
    client.xinfo_groups.return_value = [
        {"name": "workers", "pending": 1, "lag": 0, "last-delivered-id": "100-0"}
    ]
    client.xinfo_stream.return_value = {"last-generated-id": "100-0"}
    # Idle for 3.86 hours, as the stuck production entry was.
    client.xpending_range.return_value = [{"time_since_delivered": 13_901_828}]

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(client, "events") == 100


@pytest.mark.asyncio
async def test_a_freshly_pending_entry_still_protects_the_stream(monkeypatch) -> None:
    """The guard's intent is kept: real in-flight work is never trimmed through."""
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen", 100)
    monkeypatch.setattr(event_transport_settings, "redis_stream_maxlen_overrides", {})
    monkeypatch.setattr(
        event_transport_settings, "redis_stream_pending_hold_seconds", 900
    )
    client = AsyncMock()
    client.xinfo_groups.return_value = [
        {"name": "workers", "pending": 1, "lag": 0, "last-delivered-id": "100-0"}
    ]
    client.xinfo_stream.return_value = {"last-generated-id": "100-0"}
    client.xpending_range.return_value = [{"time_since_delivered": 1_000}]

    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    assert await bus._safe_publish_maxlen(client, "events") == 400
