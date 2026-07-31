from __future__ import annotations

import asyncio

import pytest

from app.modules.datastore.infrastructure.inflight_budget import InFlightByteBudget


@pytest.mark.asyncio
async def test_disabled_budget_is_a_noop():
    budget = InFlightByteBudget(0)
    # Even a huge reservation passes instantly when disabled.
    async with budget.reserve(10**12):
        pass


@pytest.mark.asyncio
async def test_lone_oversize_reservation_is_allowed():
    budget = InFlightByteBudget(100)
    # A single item larger than the whole budget must not deadlock when nothing
    # else is in flight.
    async with budget.reserve(1000):
        pass


@pytest.mark.asyncio
async def test_reservation_blocks_until_room_frees():
    budget = InFlightByteBudget(100)
    order: list[str] = []

    async def second() -> None:
        async with budget.reserve(80):
            order.append("second-enter")

    async with budget.reserve(80):
        task = asyncio.create_task(second())
        await asyncio.sleep(0.02)
        # 80 + 80 > 100 and something is in flight → the second reservation waits.
        assert not task.done()
        order.append("first-exit")

    await asyncio.wait_for(task, timeout=1)
    assert order == ["first-exit", "second-enter"]


@pytest.mark.asyncio
async def test_concurrent_small_reservations_do_not_block():
    budget = InFlightByteBudget(100)

    async def take(n: int) -> None:
        async with budget.reserve(n):
            await asyncio.sleep(0.01)

    # 40 + 40 <= 100 → both proceed concurrently without waiting.
    await asyncio.wait_for(asyncio.gather(take(40), take(40)), timeout=1)
