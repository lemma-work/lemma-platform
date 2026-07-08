from __future__ import annotations

import asyncio
import threading

import pytest

from app.core.concurrency import offload
from app.core.concurrency.offload import get_limiter, run_blocking


@pytest.fixture(autouse=True)
def _reset_limiters():
    offload.reset_limiters_for_test()
    yield
    offload.reset_limiters_for_test()


@pytest.mark.asyncio
async def test_run_blocking_runs_off_the_event_loop_thread():
    loop_thread = threading.get_ident()

    def work() -> int:
        # Executed in a worker thread, not the loop thread.
        assert threading.get_ident() != loop_thread
        return 42

    assert await run_blocking(work, limiter="cpu_bound") == 42


@pytest.mark.asyncio
async def test_run_blocking_forwards_args_and_kwargs():
    def add(a: int, b: int, *, c: int) -> int:
        return a + b + c

    assert await run_blocking(add, 1, 2, c=3, limiter="cpu_bound") == 6


@pytest.mark.asyncio
async def test_named_limiter_bounds_concurrency():
    # cpu_bound defaults to a limit >= 1; force it to 1 to observe serialization.
    limiter = get_limiter("cpu_bound")
    limiter.total_tokens = 1

    active = 0
    peak = 0
    lock = threading.Lock()
    started = asyncio.Event()

    def work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        # Busy just long enough for a second task to try to start.
        for _ in range(100000):
            pass
        with lock:
            active -= 1

    async def run_one():
        started.set()
        await run_blocking(work, limiter="cpu_bound")

    await asyncio.gather(run_one(), run_one(), run_one())
    # With a 1-token limiter, offloads never overlap.
    assert peak == 1


def test_unknown_limiter_raises():
    with pytest.raises(ValueError):
        get_limiter("does-not-exist")
