"""Holding a connection, and the two ways that goes wrong under concurrency.

Neither of these had a test when the hold was written; both were found in
review. They are about what happens when a lifecycle operation and a connect
overlap, which is the ordinary case rather than a corner: the browser pane opens
two sockets, and the idle sweep pauses sandboxes while requests are in flight.
"""

from __future__ import annotations

import asyncio

import pytest

from app.modules.workspace.providers.e2b_connections import SandboxConnections


class _Sandbox:
    def __init__(self, tag: str) -> None:
        self.tag = tag


async def test_a_burst_shares_one_connection() -> None:
    pool = SandboxConnections()
    opened = 0

    async def connect() -> object:
        nonlocal opened
        opened += 1
        await asyncio.sleep(0)
        return _Sandbox(f"s{opened}")

    got = await asyncio.gather(*(pool.get("sbx", connect) for _ in range(8)))

    assert opened == 1, f"eight callers opened {opened} connections"
    assert len({id(s) for s in got}) == 1


async def test_a_connect_that_finishes_after_a_forget_is_not_published() -> None:
    """`release` pauses a sandbox, then forgets it.

    A connect that began before the pause and lands after it would otherwise
    restore a connection to a sandbox that is no longer running -- exactly what
    `forget` exists to prevent, reintroduced by a race. The next caller must
    connect afresh, because connecting is also what resumes a paused sandbox.
    """
    pool = SandboxConnections()
    started = asyncio.Event()
    release = asyncio.Event()
    opened = 0

    async def slow_connect() -> object:
        nonlocal opened
        opened += 1
        started.set()
        await release.wait()
        return _Sandbox(f"s{opened}")

    first = asyncio.create_task(pool.get("sbx", slow_connect))
    await started.wait()
    pool.forget("sbx")  # the pause landed while the connect was in flight
    release.set()
    await first

    async def quick_connect() -> object:
        nonlocal opened
        opened += 1
        return _Sandbox(f"s{opened}")

    await pool.get("sbx", quick_connect)
    assert opened == 2, "the stale connection was published and then reused"


async def test_forget_does_not_strip_a_lock_another_caller_is_holding() -> None:
    """Removing the lock lets a second caller connect alongside the first.

    Which is the whole thing the lock is for: without it both sockets of a pane
    pay the full resume, in parallel, against the same sandbox.
    """
    pool = SandboxConnections()
    inside = asyncio.Event()
    release = asyncio.Event()
    concurrent = 0
    peak = 0

    async def connect() -> object:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        inside.set()
        await release.wait()
        concurrent -= 1
        return _Sandbox("s")

    first = asyncio.create_task(pool.get("sbx", connect))
    await inside.wait()
    pool.forget("sbx")
    second = asyncio.create_task(pool.get("sbx", connect))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)

    assert peak == 1, f"{peak} connects ran at once despite the lock"


async def test_expired_entries_do_not_accumulate() -> None:
    """One process serves every user's sandbox, so "never removed" is unbounded."""
    pool = SandboxConnections(rearm_seconds=-1.0)

    async def connect() -> object:
        return _Sandbox("s")

    for index in range(25):
        await pool.get(f"sbx-{index}", connect)

    # Bounded, not empty: the sweep runs on the way *into* `get`, so the entry
    # the last call just made is still there. One is the point -- before this,
    # all twenty-five stayed, and so did a lock apiece.
    assert len(pool._held) <= 1, f"{len(pool._held)} expired connections kept"
    assert pool._opening == {}, "locks were kept for sandboxes nobody is using"
    assert pool._waiting == {}
    assert len(pool._epoch) <= 1


@pytest.mark.parametrize("ended", ["forget", "expiry"])
async def test_the_next_caller_connects_again(ended: str) -> None:
    pool = SandboxConnections(rearm_seconds=0.0 if ended == "expiry" else 60.0)
    opened = 0

    async def connect() -> object:
        nonlocal opened
        opened += 1
        return _Sandbox(f"s{opened}")

    await pool.get("sbx", connect)
    if ended == "forget":
        pool.forget("sbx")
    await pool.get("sbx", connect)

    assert opened == 2
