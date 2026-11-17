"""Run slots: taking one is atomic, and a parked conversation keeps its own.

Against fakeredis with Lua, so the script that takes a slot is the one that
runs in production.
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import fakeredis
import pytest

from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services.run_slots import (
    LEASE_SECONDS,
    PARKED_LEASE_SECONDS,
    RunSlots,
)


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def test_concurrent_runs_never_take_more_slots_than_there_are(redis) -> None:
    """Reading the count and adding in two round trips let two runs both see
    the last free slot and both take it."""
    machine = uuid4()
    slots = RunSlots(redis=redis)

    async def one() -> bool:
        try:
            await slots.acquire(machine, uuid4(), slots=2, wait_seconds=0)
        except MachineConflict:
            return False
        return True

    taken = await asyncio.gather(*(one() for _ in range(12)))

    assert sum(taken) == 2
    assert len(await slots.holders(machine)) == 2


async def test_a_run_that_holds_a_slot_renews_it(redis) -> None:
    machine, run = uuid4(), uuid4()
    slots = RunSlots(redis=redis)
    await slots.acquire(machine, run, slots=1, wait_seconds=0)

    await slots.acquire(machine, run, slots=1, wait_seconds=0)

    assert await slots.holders(machine) == [str(run)]


async def test_busy_says_there_is_no_queue(redis) -> None:
    machine = uuid4()
    slots = RunSlots(redis=redis)
    await slots.acquire(machine, uuid4(), slots=1, wait_seconds=0)

    with pytest.raises(MachineConflict) as busy:
        await slots.acquire(machine, uuid4(), slots=1, wait_seconds=0)

    assert busy.value.code == "MACHINE_BUSY"
    assert "no queue" in str(busy.value)


async def test_a_parked_conversation_keeps_its_slot_past_the_lease(redis) -> None:
    machine, run = uuid4(), uuid4()
    slots = RunSlots(redis=redis)
    await slots.acquire(machine, run, slots=1, wait_seconds=0)

    await slots.park(run)

    lease = await redis.zscore(f"machine:slots:v1:{machine}", str(run))
    assert lease > time.time() + LEASE_SECONDS
    assert lease == pytest.approx(time.time() + PARKED_LEASE_SECONDS, abs=60)


async def test_parking_takes_no_slot_the_run_had_lost(redis) -> None:
    machine, run = uuid4(), uuid4()
    slots = RunSlots(redis=redis)
    await slots.acquire(machine, run, slots=1, wait_seconds=0)
    await slots.release(machine, run)

    await slots.park(run)

    assert await slots.holders(machine) == []
