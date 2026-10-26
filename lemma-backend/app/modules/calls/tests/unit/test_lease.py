"""One connection holds a call: a reconnect takes it over, and the one it
replaced can neither take it back nor let go of it for the new holder.

Against fakeredis running the lease's own Lua, so the compare-and-write is the
real script rather than a stand-in for it.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import fakeredis
import pytest

from app.modules.calls.services.lease import CallLease

pytestmark = pytest.mark.unit


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def _lease(call_id, redis: fakeredis.FakeAsyncRedis) -> CallLease:
    return CallLease(call_id, redis=redis, handover_seconds=0.3)


async def test_a_reconnect_takes_the_call_and_the_old_connection_learns_it(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    call_id = uuid4()
    old, new = _lease(call_id, redis), _lease(call_id, redis)
    await old.take()
    assert await old.still_held()
    await old.release()  # the old session finished before the handover wait
    await old.take()

    started = asyncio.get_running_loop().time()
    await new.take()
    # The old session was still running, so the new one waited (bounded).
    assert asyncio.get_running_loop().time() - started >= 0.25
    assert not await old.still_held()
    assert await new.still_held()


async def test_a_replaced_holder_cannot_write_itself_back_or_free_the_new_one(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    call_id = uuid4()
    old, new = _lease(call_id, redis), _lease(call_id, redis)
    await old.take()
    await old.release()
    await new.take()
    # Renewing and releasing from the old holder leave the new one in place.
    assert not await old.still_held()
    await old.release()
    assert await new.still_held()
    await new.release()
    assert await redis.get(f"calls:lease:{call_id}") is None


async def test_a_new_holder_waits_for_the_old_session_to_finish(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    call_id = uuid4()
    old, new = _lease(call_id, redis), _lease(call_id, redis)
    await old.take()

    async def finish_soon() -> None:
        await asyncio.sleep(0.05)
        await old.release()

    finishing = asyncio.create_task(finish_soon())
    started = asyncio.get_running_loop().time()
    await new.take()
    await finishing
    waited = asyncio.get_running_loop().time() - started
    # Waited for the old session to end, but not the whole handover limit.
    assert 0.04 <= waited < 0.3
