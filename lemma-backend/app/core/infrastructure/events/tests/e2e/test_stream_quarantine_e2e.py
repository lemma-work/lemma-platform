"""A poison message must leave the pending-entries list. Proved against real Redis.

Everything about this failure lives below the application: whether a handler
that raises gets acknowledged, whether an unacknowledged entry stays pending,
and whether ``XAUTOCLAIM`` hands it back. None of that can be observed with a
fake broker, which is exactly why the defect shipped — the surface e2e suite
publishes to Redis and calls handler logic separately, and its own docstring
says "there is no consumer wired into the e2e test client".

So this runs a real ``RedisBroker``, with the real subscriber decorator and the
real quarantine middleware, against a real Redis, and asserts on ``XPENDING``.

It also pins the one assumption the unit tests cannot check: that swallowing an
exception in ``consume_scope`` leaves FastStream's acknowledgement middleware —
which sits *outside* ours — seeing a clean return, and therefore acking. If
FastStream ever reorders those, the counts below go non-zero and this fails.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import redis.asyncio as redis_asyncio
from faststream.redis import RedisBroker, RedisRouter
from pydantic import BaseModel

from app.core.infrastructure.events.quarantine import (
    StreamQuarantineMiddleware,
    dead_letter_stream,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_STREAM = "quarantine_probe_events"
_GROUP = "quarantine-probe"
_CONSUMER = "quarantine-probe-consumer"

#: Long enough for the subscriber's 500ms poll plus processing, short enough
#: that a genuine hang fails the test rather than hanging the suite.
_SETTLE_SECONDS = 6.0


class _Probe(BaseModel):
    """Validating ``{}`` against this raises the same error class production hit."""

    source: str
    payload: dict


async def _pending_count(client: redis_asyncio.Redis) -> int:
    summary = await client.xpending(_STREAM, _GROUP)
    # redis-py returns a dict for the summary form.
    return int(summary["pending"] if isinstance(summary, dict) else summary[0])


async def _wait_for(predicate, *, timeout: float = _SETTLE_SECONDS) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.2)
    return False


@pytest.fixture(autouse=True)
def _quarantine_writes_to_the_same_redis(test_redis_url, monkeypatch):
    """Point the app-wide client at the test container.

    In production the broker and ``get_redis()`` resolve to one Redis, because
    both read ``settings.redis_url``. Under test the broker is handed the
    container URL directly while the settings still say ``localhost:6379``, so
    without this the dead letter is written to a different server and the
    assertions read an empty stream.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "redis_url", test_redis_url)


@pytest.fixture
async def redis_client(test_redis_url):
    client = redis_asyncio.from_url(test_redis_url, decode_responses=True)
    for key in (_STREAM, dead_letter_stream(_STREAM)):
        await client.delete(key)
    try:
        yield client
    finally:
        for key in (_STREAM, dead_letter_stream(_STREAM)):
            await client.delete(key)
        await client.aclose()


@pytest.fixture
async def running_broker(test_redis_url, redis_client):
    """The real broker, decorator and middleware — nothing stubbed."""
    handled: list[dict] = []
    router = RedisRouter()

    @reliable_redis_stream_subscriber(
        router, _STREAM, group=_GROUP, consumer=_CONSUMER
    )
    async def handler(event: dict) -> None:
        # Mirrors every real consumer: take the envelope untyped, then parse.
        if event.get("event_type") != "probe.wanted":
            return
        if event.get("explode"):
            # A real pydantic failure, because that is what actually happened:
            # the surface_events poison messages were ValidationErrors. A bare
            # ValueError would be classified transient and retried, which is the
            # deliberate design — the permanent set is closed and small.
            _Probe.model_validate({})
        handled.append(event)

    broker = RedisBroker(
        test_redis_url, middlewares=(StreamQuarantineMiddleware,)
    )
    broker.include_router(router)
    await broker.start()
    try:
        yield broker, handled
    finally:
        await broker.stop()


async def _publish(client: redis_asyncio.Redis, payload: dict) -> None:
    # XADD directly rather than through the broker, so the message on the stream
    # is exactly the bytes a producer would write.
    await client.xadd(_STREAM, {"__data__": json.dumps(payload)})


async def test_a_healthy_message_is_processed_and_acknowledged(
    running_broker, redis_client
):
    _, handled = running_broker

    await _publish(redis_client, {"event_type": "probe.wanted", "id": "good-1"})

    assert await _wait_for(lambda: _truthy(handled)), "handler never ran"
    assert await _wait_for(lambda: _pending_is(redis_client, 0)), (
        "a successfully handled message was never acknowledged"
    )


async def test_an_event_this_consumer_does_not_want_still_leaves_the_pel(
    running_broker, redis_client
):
    """The RC-1 shape: a shared stream carrying another consumer's event.

    ``surface_events`` carries ``surface.connected`` for the analytics
    projections. The webhook consumer must ignore it *and* acknowledge it. It
    used to do neither, because a typed parameter failed validation before the
    ack and the reclaimer then returned the message every 60 seconds forever.
    """
    _, handled = running_broker

    await _publish(redis_client, {"event_type": "probe.unwanted", "id": "other-1"})

    assert await _wait_for(lambda: _pending_is(redis_client, 0)), (
        "an ignored event stayed pending — this is the poison loop"
    )
    assert handled == []


async def test_a_message_that_can_never_succeed_is_dead_lettered_not_retried(
    running_broker, redis_client
):
    """The audit's actual ask, end to end."""
    await _publish(
        redis_client,
        {"event_type": "probe.wanted", "id": "poison-1", "explode": True},
    )

    dead = dead_letter_stream(_STREAM)
    assert await _wait_for(lambda: _stream_len_at_least(redis_client, dead, 1)), (
        "the poison message was never dead-lettered"
    )
    assert await _wait_for(lambda: _pending_is(redis_client, 0)), (
        "the poison message was dead-lettered but never acknowledged, so it is "
        "still in the PEL and will be reclaimed forever"
    )

    entries = await redis_client.xrange(dead)
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields["original_stream"] == _STREAM
    assert fields["consumer_groups"] == _GROUP
    assert "poison-1" in fields["body"]


async def test_a_poison_message_does_not_hold_up_the_next_one(
    running_broker, redis_client
):
    """Quarantine must drain the stream, not stall behind the bad entry."""
    _, handled = running_broker

    await _publish(
        redis_client,
        {"event_type": "probe.wanted", "id": "poison-2", "explode": True},
    )
    await _publish(redis_client, {"event_type": "probe.wanted", "id": "good-2"})

    assert await _wait_for(
        lambda: _handled_ids(handled, {"good-2"})
    ), "a healthy message queued behind a poison one was never processed"
    assert await _wait_for(lambda: _pending_is(redis_client, 0))


# -- small async predicates, kept out of the tests for readability -----------


async def _truthy(collection) -> bool:
    return bool(collection)


async def _pending_is(client, expected: int) -> bool:
    return await _pending_count(client) == expected


async def _stream_len_at_least(client, stream: str, count: int) -> bool:
    return int(await client.xlen(stream)) >= count


async def _handled_ids(handled, expected: set[str]) -> bool:
    return expected.issubset({event.get("id") for event in handled})
