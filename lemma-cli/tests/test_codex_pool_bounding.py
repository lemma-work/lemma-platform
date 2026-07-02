"""Tests for CodexWorkerPool's per-daemon concurrency bounding (slot semaphore).

Drives the pool's internal ``_worker_for_conversation``/``_retire_worker``/
``_close_after_ttl`` directly rather than through a full simulated codex
app-server turn -- those methods are exactly where the slot-accounting logic
lives, and the full turn machinery (JsonRpcProcess protocol, etc.) is already
covered by the existing tests in test_daemon_cli.py.
"""

from __future__ import annotations

import asyncio

import pytest

from lemma_cli.daemon.harnesses import codex


def test_codex_pool_max_workers_defaults(monkeypatch):
    monkeypatch.delenv(codex.CODEX_POOL_MAX_WORKERS_ENV, raising=False)
    assert codex.codex_pool_max_workers() == 4


def test_codex_pool_max_workers_env_override(monkeypatch):
    monkeypatch.setenv(codex.CODEX_POOL_MAX_WORKERS_ENV, "2")
    assert codex.codex_pool_max_workers() == 2


def test_codex_pool_max_workers_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(codex.CODEX_POOL_MAX_WORKERS_ENV, "not-a-number")
    assert codex.codex_pool_max_workers() == 4


@pytest.mark.asyncio
async def test_worker_for_conversation_reuses_existing_without_consuming_new_slot(monkeypatch):
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    pool = codex.CodexWorkerPool()

    worker1 = await pool._worker_for_conversation("conv-1")
    # The pool is at capacity (1/1) via conv-1's slot -- a second call for the
    # SAME conversation must reuse it without blocking on the semaphore (the
    # fast path in _worker_for_conversation returns before ever touching
    # self._slots).
    worker2 = await asyncio.wait_for(pool._worker_for_conversation("conv-1"), timeout=0.2)

    assert worker1 is worker2


@pytest.mark.asyncio
async def test_worker_for_new_conversation_blocks_when_pool_full(monkeypatch):
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    pool = codex.CodexWorkerPool()

    await pool._worker_for_conversation("conv-1")  # consumes the only slot

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(pool._worker_for_conversation("conv-2"), timeout=0.1)


@pytest.mark.asyncio
async def test_worker_for_new_conversation_proceeds_once_a_slot_frees(monkeypatch):
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    pool = codex.CodexWorkerPool()

    worker1 = await pool._worker_for_conversation("conv-1")
    waiter = asyncio.create_task(pool._worker_for_conversation("conv-2"))
    await asyncio.sleep(0.05)
    assert not waiter.done()  # still queued, pool is full

    await pool._retire_worker("conv-1", worker1)  # frees the slot

    worker2 = await asyncio.wait_for(waiter, timeout=1.0)
    assert worker2.conversation_id == "conv-2"


@pytest.mark.asyncio
async def test_slot_released_on_worker_retirement_after_error(monkeypatch):
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    pool = codex.CodexWorkerPool()

    worker1 = await pool._worker_for_conversation("conv-1")
    # Simulates the error path in CodexWorkerPool.run(): a turn raises, the
    # worker is retired immediately (not left to idle-TTL out).
    await pool._retire_worker("conv-1", worker1)

    # A distinct-conversation call must proceed immediately -- the freed slot
    # let it straight through, and it's a genuinely different worker object.
    worker2 = await asyncio.wait_for(pool._worker_for_conversation("conv-2"), timeout=0.2)
    assert worker2 is not worker1
    assert worker2.conversation_id == "conv-2"


@pytest.mark.asyncio
async def test_retire_worker_is_a_noop_if_already_retired(monkeypatch):
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    pool = codex.CodexWorkerPool()

    worker1 = await pool._worker_for_conversation("conv-1")
    await pool._retire_worker("conv-1", worker1)
    worker2 = await pool._worker_for_conversation("conv-2")  # takes the freed slot

    # Retiring the ALREADY-retired conv-1 worker a second time (e.g. a
    # duplicate error-path call) must not release a slot that isn't rightfully
    # held anymore -- if it did, a third distinct conversation would
    # incorrectly get through despite the pool's cap of 1.
    await pool._retire_worker("conv-1", worker1)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(pool._worker_for_conversation("conv-3"), timeout=0.1)

    # Sanity: conv-2's own slot is unaffected by any of this.
    assert (await pool._worker_for_conversation("conv-2")) is worker2


@pytest.mark.asyncio
async def test_slot_released_on_idle_ttl_eviction(monkeypatch):
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    monkeypatch.setattr(codex, "codex_worker_ttl_seconds", lambda: 0.02)
    pool = codex.CodexWorkerPool()

    worker1 = await pool._worker_for_conversation("conv-1")
    pool._schedule_idle_close("conv-1", worker1)

    # Before the TTL fires, the pool is still full.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(pool._worker_for_conversation("conv-2"), timeout=0.01)

    # After it fires, the slot is free.
    worker2 = await asyncio.wait_for(pool._worker_for_conversation("conv-2"), timeout=1.0)
    assert worker2.conversation_id == "conv-2"


@pytest.mark.asyncio
async def test_slot_not_double_released_when_worker_reused_before_ttl_fires(monkeypatch):
    """Regression test for the trickiest edge case: reusing a conversation's
    worker before its scheduled idle-close fires must NOT cause the eventual
    real close to release a slot the reuse never re-consumed (a double
    release would let one extra conversation through beyond the cap).
    """
    monkeypatch.setattr(codex, "codex_pool_max_workers", lambda: 1)
    monkeypatch.setattr(codex, "codex_worker_ttl_seconds", lambda: 0.05)
    pool = codex.CodexWorkerPool()

    worker1 = await pool._worker_for_conversation("conv-1")
    pool._schedule_idle_close("conv-1", worker1)  # simulates turn #1 finishing

    await asyncio.sleep(0.01)
    # Reuse before the TTL fires -- cancels + (via the next line) reschedules
    # the pending close, exactly like a second turn on the same conversation.
    worker1_again = await pool._worker_for_conversation("conv-1")
    assert worker1_again is worker1
    pool._schedule_idle_close("conv-1", worker1)  # simulates turn #2 finishing

    # Let the (rescheduled) TTL close actually fire.
    await asyncio.sleep(0.15)

    # The slot was released exactly once by the real close -- a second
    # conversation can now get in...
    worker2 = await asyncio.wait_for(pool._worker_for_conversation("conv-2"), timeout=0.5)
    assert worker2.conversation_id == "conv-2"

    # ...but a THIRD must NOT, because the pool is still only 1-wide. If the
    # stale (cancelled) close had also released a slot, both conv-2 and
    # conv-3 would get through here.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(pool._worker_for_conversation("conv-3"), timeout=0.1)
