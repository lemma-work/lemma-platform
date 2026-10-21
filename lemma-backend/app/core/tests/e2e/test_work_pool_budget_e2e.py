"""The runtime at full concurrency never needs more connections than the pool it sized.

Every slot busy: fast handlers holding their transaction, jobs heartbeating on
a short lease while they checkpoint and complete, and follow-ups enqueued into
namespaces that have no queue row yet (the side connection). The pool is
exactly the budget with a short checkout timeout, so one connection taken
outside the budget would surface as a pool timeout -- a charged attempt, a
lease loss, or a settle failure.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.infrastructure.work import config as work_config
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.db import DbBudget, PoolTooSmall
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.runtime import WorkRuntime
from app.core.infrastructure.work.types import WorkClass, WorkMode
from app.core.tests.e2e.work_support import POD_A, Demo, WorkDb, write_effect
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

BUDGET = DbBudget({WorkClass.EVENTS: 3, WorkClass.AUTOMATION: 2}, control=2)
FAST_ITEMS = 300
JOBS = 64


async def test_every_slot_busy_never_waits_past_the_pool(
    work_db: WorkDb, registry: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        work_config.work_settings, "work_slots_json", '{"events": 32, "automation": 32}'
    )
    monkeypatch.setattr(work_config.work_settings, "work_poll_interval_seconds", 0.05)
    url = work_db.engine.url.render_as_string(hide_password=False)
    engine = create_async_engine(
        url, pool_size=BUDGET.total, max_overflow=0, pool_timeout=2
    )
    peak = 0

    @event.listens_for(engine.sync_engine.pool, "checkout")
    def _checked_out(*_args: object) -> None:
        nonlocal peak
        peak = max(peak, engine.sync_engine.pool.checkedout())

    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @registry.handler(
        kind="demo.fast", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def fast(ctx: FastContext, payload: Demo) -> None:
        await ctx.uow.session.execute(text("SELECT pg_sleep(0.02)"))
        await write_effect(ctx.uow.session, "fast", ctx.item.id)
        if payload.tag == "spread":
            # A namespace with no queue row: the writer's side connection.
            await ctx.work.add("demo.leaf", f"pod:{uuid4()}", Demo())

    @registry.handler(
        kind="demo.leaf", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def leaf(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, "leaf", ctx.item.id)

    @registry.handler(
        kind="demo.job",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        # A heartbeat every second. Tighter (0.6 s) and 64 jobs heartbeating
        # through two automation connections timed out on a loaded runner:
        # leases were reaped, re-queued with backoff, and one outlasted the
        # wait below. Lease loss is not what this test is about.
        lease=timedelta(seconds=3),
    )
    async def job(ctx: JobContext, payload: Demo) -> None:
        async with ctx.checkpoint() as uow:
            await write_effect(uow.session, "job.start", ctx.item.id)
        await asyncio.sleep(1.5)  # holds no connection, keeps heartbeating
        async with ctx.complete_work() as uow:
            await write_effect(uow.session, "job.done", ctx.item.id)

    for i in range(FAST_ITEMS):
        tag = "spread" if i % 10 == 0 else ""
        await work_db.enqueue(
            registry, "demo.fast", f"pod:{uuid4()}" if i % 3 else POD_A, Demo(tag=tag)
        )
    for _ in range(JOBS):
        await work_db.enqueue(registry, "demo.job", f"pod:{uuid4()}")
    # Caps never bind, so every slot can be busy at once.
    await work_db.execute("UPDATE work_queues SET max_running = 1000")

    runtime = WorkRuntime(
        sessions,
        classes=[WorkClass.EVENTS, WorkClass.AUTOMATION],
        registry=registry,
        budget=BUDGET,
    )
    await runtime.start()
    try:
        await eventually(
            label="every item finished",
            probe=work_db.count,
            done=lambda n: n == 0,
            timeout_seconds=60,
        )
    finally:
        await runtime.stop(grace_seconds=1)
        await engine.dispose()

    effects = await work_db.effects()
    assert effects.count("fast") == FAST_ITEMS
    assert effects.count("leaf") == FAST_ITEMS // 10
    assert effects.count("job.done") == JOBS
    assert peak <= BUDGET.total
    print(f"peak connections {peak} of {BUDGET.total}")


async def test_a_runtime_given_a_pool_smaller_than_its_budget_refuses_to_start(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    url = work_db.engine.url.render_as_string(hide_password=False)
    engine = create_async_engine(url, pool_size=BUDGET.total - 1, max_overflow=0)
    try:
        with pytest.raises(PoolTooSmall):
            WorkRuntime(
                async_sessionmaker(engine),
                classes=[WorkClass.EVENTS, WorkClass.AUTOMATION],
                registry=registry,
                budget=BUDGET,
            )
    finally:
        await engine.dispose()
