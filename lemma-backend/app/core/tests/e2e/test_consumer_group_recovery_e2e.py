"""What happens to a running subscriber when its consumer group disappears.

A Redis restart without persistence, a failover to an un-replicated replica, an
eviction, a stray ``FLUSHDB`` -- all of them delete the consumer group out from
under a live subscriber, and the subscriber's next XREADGROUP answers NOGROUP.
FastStream's own message is "Stopping subscriber -- restart the application to
recreate the group", and the code carried two comments that disagreed about
what that meant: one said a stopped subscriber could not be revived, the other
said the reconcile loop self-heals with no manual restart.

Only one of those can be true, and it cannot be settled by reading: it depends
on what the installed FastStream does with a consume task that raised. So this
settles it against a real Redis and a real broker. The reconcile loop's claim is
the true one -- ``TasksMixin.add_task`` attaches a supervisor that restarts the
consume task whatever it raised -- and recreating the group is what lets the
next attempt succeed.

If a FastStream upgrade ever changes that, this test fails and the reconcile
loop's docstring stops being a promise the code cannot keep.
"""

from __future__ import annotations


import pytest
from faststream.redis import RedisBroker, RedisRouter
from redis.asyncio import Redis

from app.core.infrastructure.events.stream_subscriber import (
    ensure_consumer_groups,
    redis_stream_sub,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures

from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]

redis_container = e2e_fixtures.redis_container
test_redis_url = e2e_fixtures.test_redis_url

_STREAM = "consumer_group_recovery_e2e"
_GROUP = "consumer-group-recovery-e2e"

# Generous next to the 500ms subscriber poll: this waits on a real container.
_DELIVERY_TIMEOUT_SECONDS = 20.0


async def _await_delivery(received: list[str], expected: str) -> None:
    await eventually(
        label=f"{expected!r} delivered on {_STREAM}",
        probe=lambda: _snapshot(received),
        done=lambda seen: expected in seen,
        timeout_seconds=_DELIVERY_TIMEOUT_SECONDS,
        interval_seconds=0.05,
    )


async def _snapshot(received: list[str]) -> list[str]:
    """A copy, so the waiter never reads a list the subscriber is appending to."""
    return list(received)


async def test_a_deleted_consumer_group_is_recreated_and_delivery_resumes(
    test_redis_url: str, monkeypatch
):
    from app.core.infrastructure.events import stream_subscriber

    # Only this test's stream, so the pass does not touch the topology every
    # other subscriber in the process registered at import time.
    monkeypatch.setattr(
        stream_subscriber, "_REGISTERED_STREAM_GROUPS", {(_STREAM, _GROUP)}
    )
    monkeypatch.setattr(stream_subscriber, "_DECLARED_STREAM_GROUPS", set())

    received: list[str] = []
    router = RedisRouter()

    @router.subscriber(
        stream=redis_stream_sub(_STREAM, group=_GROUP, consumer="recovery-e2e")
    )
    async def _handler(event: dict) -> None:
        received.append(str(event.get("marker")))

    broker = RedisBroker(test_redis_url)
    broker.include_router(router)
    redis = Redis.from_url(test_redis_url)

    await broker.start()
    try:
        await broker.publish({"marker": "before"}, stream=_STREAM)
        await _await_delivery(received, "before")

        # The accident: the group is gone, the stream is not.
        await redis.xgroup_destroy(name=_STREAM, groupname=_GROUP)
        # Published into the gap. Nothing is listening, so it is not delivered
        # -- which is why the publisher path ensures the group before XADD
        # (see message_bus._safe_publish) rather than relying on this loop.
        await broker.publish({"marker": "during"}, stream=_STREAM)

        # One reconcile tick, exactly what the worker's loop runs.
        assert await ensure_consumer_groups(redis) == 1

        await broker.publish({"marker": "after"}, stream=_STREAM)
        # The subscriber resumed on its own: no restart, no new broker.
        await _await_delivery(received, "after")
    finally:
        await redis.delete(_STREAM)
        await redis.aclose()
        await broker.stop()
