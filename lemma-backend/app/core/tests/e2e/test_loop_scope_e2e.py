"""Prove the loop-stall detector catches blocking work, and stays quiet without it.

The unit tests for the sampler drive its internals. This proves the fixture
wiring: that a real blocking call inside a real async test is caught, and that
the same work moved off the loop is not.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.modules.test_support import loop_scope as loop_scope_support

pytestmark = [pytest.mark.e2e, pytest.mark.connection_scope]

strict_loop_stalls = loop_scope_support.strict_loop_stalls

# Comfortably past the fixture's 0.5s threshold, so the assertion is about
# behaviour and not about how fast the machine is.
_BLOCK_SECONDS = 1.2


def _cpu_bound_work(seconds: float) -> int:
    """Stands in for chunking, regex passes, a per-character loop."""
    deadline = time.monotonic() + seconds
    spins = 0
    while time.monotonic() < deadline:
        spins += 1
    return spins


async def test_blocking_the_loop_is_caught(strict_loop_stalls) -> None:
    _cpu_bound_work(_BLOCK_SECONDS)
    await asyncio.sleep(0.2)  # let the sampler's thread observe and report

    assert strict_loop_stalls.reports >= 1, "a blocked event loop went unnoticed"
    # Do not let the fixture fail the test for the stall we caused on purpose.
    strict_loop_stalls.reports = 0


async def test_the_same_work_offloaded_is_silent(strict_loop_stalls) -> None:
    """The prescribed fix must be silent, or the gate teaches people to avoid it."""
    from app.core.concurrency.offload import run_blocking

    await run_blocking(_cpu_bound_work, _BLOCK_SECONDS, limiter="cpu_bound")
    await asyncio.sleep(0.2)

    assert strict_loop_stalls.reports == 0


async def test_ordinary_async_work_is_silent(strict_loop_stalls) -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)

    assert strict_loop_stalls.reports == 0
