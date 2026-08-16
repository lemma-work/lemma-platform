"""Fail a test that blocks the event loop.

The sibling of ``connection_scope``: that one catches a pooled connection held
across non-database work, this one catches work that stops the loop entirely.
Both exist because the static gates cannot see everything — ``make lint-async``
knows the sync I/O primitives and nothing about CPU, so a per-character loop
over a document or thirteen regex passes over 8 MiB is invisible to it.

``LoopStallSampler`` (from #349) already does the hard part: it watches from an
OS thread, so it runs *during* the stall and captures the stack of the call that
is blocking rather than the scaffolding around it. All this adds is a test
verdict and a tick to watch.

The threshold is deliberately looser than the connection one. A stalled loop is
measured in wall clock on a machine that is also running Postgres, Redis and
possibly Docker, so a tight bound would report the machine rather than the code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.core.observability import stall_sampler
from app.core.observability.stall_sampler import LoopStallSampler

# Long enough that ordinary test scheduling never trips it, short enough to
# catch the findings this exists for — the smallest measured one was 74ms, but
# those run inside a request that does other work, and the audit's CPU items are
# hundreds of milliseconds to seconds.
STRICT_STALL_SECONDS = 0.5

# How often the loop publishes "I am still being scheduled". Must be well under
# the threshold or a healthy loop looks stalled between ticks.
_TICK_SECONDS = 0.05


@pytest.fixture
async def strict_loop_stalls() -> AsyncIterator[LoopStallSampler]:
    """Fail this test if the event loop stops being scheduled.

    Async so it is set up *inside* the loop it watches: the tick task has to
    live on that loop, and a sync fixture runs before there is one.

    Opt-in for the same reason ``strict_connection_scope`` is: the suite is not
    clean yet, and an autouse gate over a suite with known findings becomes an
    opt-out flag within a week.
    """
    sampler = stall_sampler.start_loop_stall_sampler(
        stall_seconds=STRICT_STALL_SECONDS, service_name="pytest"
    )
    # No cooldown: a test wants every stall, not one per minute.
    sampler._cooldown_seconds = 0.0  # noqa: SLF001
    ticker = asyncio.get_running_loop().create_task(
        stall_sampler.keep_loop_tick_fresh(sampler, _TICK_SECONDS)
    )
    try:
        yield sampler
    finally:
        ticker.cancel()
        stall_sampler.stop_loop_stall_sampler()

    if sampler.reports:
        pytest.fail(
            f"the event loop stalled {sampler.reports} time(s) for longer than "
            f"{STRICT_STALL_SECONDS * 1000:.0f}ms; the blocking call's own stack "
            f"was logged as runtime.loop_stall.degraded",
            pytrace=False,
        )
