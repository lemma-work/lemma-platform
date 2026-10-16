"""Recovery against real Postgres: the only recovery mechanism is "lease expired, claim again".

Every path that returns an item to ``queued`` -- lease reap, failure backoff,
shutdown release, admin retry -- must leave it claimable with no flag to fix,
and every budget must be charged only by its own cause.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.infrastructure.work.admin import retry_dead
from app.core.infrastructure.work.context import DeadContext, FastContext
from app.core.infrastructure.work.db import WorkDb as RuntimeWorkDb
from app.core.infrastructure.work.dispatcher import Dispatcher
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.retry import Exponential, RetryPolicy
from app.core.infrastructure.work.runtime import WorkRuntime
from app.core.infrastructure.work.testing import (
    dispatcher_for,
    make_due,
    run_until_idle,
)
from app.core.infrastructure.work.types import (
    Defer,
    ItemFailure,
    Released,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
)
from app.modules.test_support.e2e.waiters import eventually
from app.core.tests.e2e.work_support import (
    Demo,
    WorkDb,
    write_effect,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

NO_WAIT = Exponential(base=timedelta(0), cap=timedelta(0), jitter=0)
LATER = Exponential(base=timedelta(hours=1), jitter=0)


async def _on_dead(ctx: DeadContext) -> None:
    await write_effect(ctx.uow.session, f"terminal:{ctx.reason}", ctx.item.id)


def _poison(
    registry: WorkRegistry,
    *,
    mode: WorkMode,
    backoff: Exponential = NO_WAIT,
    **retry: object,
) -> None:
    async def fast(ctx: FastContext, payload: Demo) -> None:
        raise ItemFailure("this item is broken")

    async def job(ctx: JobContext, payload: Demo) -> None:
        raise ItemFailure("this item is broken")

    registry.handler(  # type: ignore[call-overload]
        kind="demo.poison",
        cls=WorkClass.AUTOMATION,
        mode=mode,
        payload=Demo,
        retry=RetryPolicy(backoff=backoff, **retry),  # type: ignore[arg-type]
        on_dead=_on_dead,
        lease=timedelta(seconds=30),
    )(fast if mode is WorkMode.FAST else job)


async def test_failure_backs_off_charges_attempts_then_dies_with_its_terminal_state(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _poison(registry, mode=WorkMode.FAST, backoff=LATER, max_attempts=3)
    item_id = await work_db.enqueue(registry, "demo.poison")
    for expected in (1, 2):
        await run_until_idle(work_db.sessions, registry=registry)
        row = await work_db.item(item_id)
        assert row and row["state"] == "queued" and row["attempts"] == expected
        assert "this item is broken" in str(row["last_error"])
        assert (
            await run_until_idle(work_db.sessions, registry=registry) == 0
        )  # backing off
        await make_due(work_db.sessions)
    await run_until_idle(work_db.sessions, registry=registry)
    row = await work_db.item(item_id)
    assert row and row["state"] == "dead" and row["error_class"] == "attempts_exhausted"
    assert await work_db.effects() == ["terminal:attempts_exhausted"]

    # Admin retry: budgets reset, claimable again.
    async with work_db.sessions() as session:
        assert await retry_dead(session, kind="demo.poison") == (1, 0)
        await session.commit()
    row = await work_db.item(item_id)
    assert row and row["state"] == "queued" and row["attempts"] == 0
    assert (
        len(
            await dispatcher_for(
                work_db.sessions, WorkClass.AUTOMATION, registry=registry
            ).claim_once()
        )
        == 1
    )


async def test_admin_retry_of_a_dead_item_with_a_queued_twin_deletes_it(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _poison(registry, mode=WorkMode.FAST, max_attempts=1)
    dead_id = await work_db.enqueue(registry, "demo.poison", dedup_key="same")
    await run_until_idle(work_db.sessions, registry=registry)
    twin = await work_db.enqueue(registry, "demo.poison", dedup_key="same")
    async with work_db.sessions() as session:
        assert await retry_dead(session, item_id=dead_id) == (0, 1)
        await session.commit()
    assert await work_db.item(dead_id) is None
    assert await work_db.item(twin) is not None


async def test_unavailable_charges_nothing_until_its_budget_runs_out(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.down",
        cls=WorkClass.INGESTION,
        mode=WorkMode.JOB,
        payload=Demo,
        retry=RetryPolicy(backoff=NO_WAIT, unavailable_budget=timedelta(seconds=1)),
        on_dead=_on_dead,
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        raise Unavailable(
            "health check failing", evidence=UnavailableEvidence.HEALTH_CHECK_FAILING
        )

    item_id = await work_db.enqueue(registry, "demo.down")
    await run_until_idle(work_db.sessions, registry=registry)
    first = await work_db.item(item_id)
    assert (
        first and first["attempts"] == 0 and first["first_unavailable_at"] is not None
    )
    await make_due(work_db.sessions)
    await run_until_idle(work_db.sessions, registry=registry)
    second = await work_db.item(item_id)
    assert second and second["first_unavailable_at"] == first["first_unavailable_at"]

    await work_db.execute(
        "UPDATE work_items SET first_unavailable_at = now() - interval '1 hour', run_at = now()"
    )
    await run_until_idle(work_db.sessions, registry=registry)
    row = await work_db.item(item_id)
    assert (
        row and row["state"] == "dead" and row["error_class"] == "unavailable_exhausted"
    )
    assert row["attempts"] == 0
    assert await work_db.effects() == ["terminal:unavailable_exhausted"]


async def test_defer_requeues_with_nothing_charged(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    calls = 0

    @registry.handler(
        kind="demo.gate", cls=WorkClass.AUTOMATION, mode=WorkMode.JOB, payload=Demo
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Defer(timedelta(seconds=5))

    item_id = await work_db.enqueue(registry, "demo.gate")
    await run_until_idle(work_db.sessions, registry=registry)
    row = await work_db.item(item_id)
    assert row and row["state"] == "queued" and row["attempts"] == 0
    assert await run_until_idle(work_db.sessions, registry=registry) == 0  # not due yet
    await make_due(work_db.sessions)
    assert await run_until_idle(work_db.sessions, registry=registry) == 1
    assert await work_db.item(item_id) is None


async def test_a_fast_handler_past_its_statement_timeout_is_a_charged_timeout(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.slow", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, "partial")
        await ctx.uow.session.execute(text("SELECT pg_sleep(1)"))

    item_id = await work_db.enqueue(registry, "demo.slow")
    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.EVENTS, registry=registry, statement_timeout_ms=100
    )
    [item] = await dispatcher.claim_once()
    assert await dispatcher.execute(item) == "retry"
    row = await work_db.item(item_id)
    assert row and row["error_class"] == "timeout" and row["attempts"] == 1
    assert await work_db.effects() == []


async def test_a_fast_handler_killed_mid_transaction_leaves_no_effect_and_is_reclaimed(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    hang = asyncio.Event()
    entered = asyncio.Event()

    @registry.handler(
        kind="demo.effect", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, "effect", ctx.item.id)
        entered.set()
        if not hang.is_set():
            await asyncio.Event().wait()  # killed here

    item_id = await work_db.enqueue(registry, "demo.effect")
    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)
    [item] = await dispatcher.claim_once()
    task = asyncio.create_task(dispatcher.execute(item))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await work_db.effects() == []
    row = await work_db.item(item_id)
    assert row and row["state"] == "running"

    hang.set()
    await work_db.expire_leases()
    other = dispatcher_for(
        work_db.sessions, WorkClass.EVENTS, registry=registry, owner="other"
    )
    assert await other.reap() == 1
    row = await work_db.item(item_id)
    assert (
        row
        and row["state"] == "queued"
        and row["lease_losses"] == 1
        and row["attempts"] == 0
    )
    await make_due(work_db.sessions)
    assert await run_until_idle(work_db.sessions, registry=registry) == 1
    assert await work_db.effects() == ["effect"]


async def test_lease_losses_past_their_budget_go_dead_with_the_terminal_hook(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _poison(registry, mode=WorkMode.JOB, max_lease_losses=2)
    item_id = await work_db.enqueue(registry, "demo.poison")
    crasher = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="crash"
    )
    reaper = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="reap"
    )
    for _ in range(3):
        await make_due(work_db.sessions)
        assert len(await crasher.claim_once()) == 1  # claimed, then the process "dies"
        await work_db.expire_leases()
        assert await reaper.reap() == 1
    row = await work_db.item(item_id)
    assert (
        row
        and row["state"] == "dead"
        and row["error_class"] == "lease_losses_exhausted"
    )
    assert row["lease_losses"] == 3 and row["attempts"] == 0
    assert await work_db.effects() == ["terminal:lease_losses_exhausted"]


async def test_a_heartbeat_keeps_a_job_twice_its_lease_alive(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.long",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        lease=timedelta(seconds=1),
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        await asyncio.sleep(2.5)  # silent, like a long tool call
        async with ctx.complete_work() as uow:
            await write_effect(uow.session, "finished", ctx.item.id)

    item_id = await work_db.enqueue(registry, "demo.long")
    runner = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="a"
    )
    reaper = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="b"
    )
    [item] = await runner.claim_once()
    task = asyncio.create_task(runner.execute(item))
    # Another worker reaps every tick for the whole run; it must find nothing.
    reaped = 0

    async def reap() -> bool:
        nonlocal reaped
        reaped += await reaper.reap()
        return task.done()

    await eventually(
        label="long job finishes",
        probe=reap,
        done=bool,
        timeout_seconds=10,
        interval_seconds=0.1,
    )
    assert task.result() == "done"
    assert reaped == 0
    assert await work_db.item(item_id) is None
    assert await work_db.effects() == ["finished"]


class _ControlUnreachable(RuntimeWorkDb):
    """The worker's view of a database it can no longer reach for heartbeats."""

    def control(self):  # type: ignore[override]
        return _refused()


@asynccontextmanager
async def _refused():
    raise OperationalError("SELECT 1", {}, ConnectionRefusedError())
    yield  # pragma: no cover - unreachable, keeps this a generator


async def test_a_job_cut_off_from_the_database_stops_before_its_lease_runs_out(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    """A heartbeat that gets no answer must not keep the job running past its lease.

    The lease runs out on the database's clock whether or not the worker can
    reach it, and another worker may claim the item then. So the job is
    cancelled while the last committed extension still covers it.
    """
    lease = timedelta(seconds=1)
    finished = asyncio.Event()

    @registry.handler(
        kind="demo.partitioned",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        lease=lease,
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        await asyncio.sleep(5)  # a long, silent tool call
        finished.set()

    item_id = await work_db.enqueue(registry, "demo.partitioned")
    runner = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="a"
    )
    [item] = await runner.claim_once()
    # The same process, from the moment its heartbeats stop getting through.
    partitioned = Dispatcher(
        WorkClass.AUTOMATION,
        replace(runner.env, db=_ControlUnreachable(work_db.sessions, None)),
        slots=1,
        quantum=1,
        poll_interval=0.05,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    outcome = await partitioned.execute(item)
    stopped_after = loop.time() - started

    assert outcome == "lease_lost"
    assert not finished.is_set()
    assert stopped_after < lease.total_seconds(), (
        f"the job ran {stopped_after:.2f}s on a {lease.total_seconds()}s lease "
        "it could not renew; another worker may already hold the item"
    )
    # Nothing was released or completed: the lease expires and is reaped.
    row = await work_db.item(item_id)
    assert row and row["state"] == "running"


async def test_a_zombie_whose_lease_was_taken_over_cannot_complete(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    proceed = asyncio.Event()

    @registry.handler(
        kind="demo.zombie",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        lease=timedelta(minutes=5),
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        await proceed.wait()
        async with ctx.complete_work() as uow:
            await write_effect(uow.session, f"done-by:{ctx.item.attempts}")

    item_id = await work_db.enqueue(registry, "demo.zombie")
    zombie = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="z"
    )
    [item] = await zombie.claim_once()
    stalled = asyncio.create_task(zombie.execute(item))

    # The zombie stalls past its lease; another worker reaps and claims it.
    await work_db.expire_leases()
    heir = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="h"
    )
    assert await heir.reap() == 1
    await make_due(work_db.sessions)
    [again] = await heir.claim_once()
    assert again.id == item_id

    proceed.set()
    assert await stalled == "lease_lost"
    assert await work_db.effects() == []
    row = await work_db.item(item_id)
    assert row and row["state"] == "running" and str(row["lease_owner"]).startswith("h")

    assert await heir.execute(again) == "done"
    assert await work_db.effects() == ["done-by:0"]


async def test_a_unit_checked_at_commit_commits_nothing_after_a_takeover(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    """A job's own session, read against the claim at each commit.

    Before the takeover it commits, and a unit nested inside it is not held
    up by it (nothing is locked until the commit). After it, the commit is
    refused with ``LeaseLost`` and its write rolls back.
    """
    from app.core.infrastructure.work.types import LeaseLost

    proceed = asyncio.Event()
    refused: list[BaseException] = []

    @registry.handler(
        kind="demo.checked",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        lease=timedelta(minutes=5),
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        async with work_db.sessions() as outer:
            ctx.check_lease_at_commit(outer)
            await write_effect(outer, "before")
            async with work_db.sessions() as inner:
                ctx.check_lease_at_commit(inner)
                await write_effect(inner, "nested")
                await asyncio.wait_for(inner.commit(), 5)
            await outer.commit()
        await proceed.wait()
        async with work_db.sessions() as late:
            ctx.check_lease_at_commit(late)
            await write_effect(late, "after")
            try:
                await late.commit()
            except LeaseLost as exc:
                refused.append(exc)
                raise

    item_id = await work_db.enqueue(registry, "demo.checked")
    zombie = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="z"
    )
    [item] = await zombie.claim_once()
    stalled = asyncio.create_task(zombie.execute(item))
    await eventually(
        label="the zombie's first commits",
        probe=work_db.effects,
        done=lambda effects: sorted(effects) == ["before", "nested"],
        interval_seconds=0.05,
    )

    await work_db.expire_leases()
    heir = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, owner="h"
    )
    assert await heir.reap() == 1
    await make_due(work_db.sessions)
    [again] = await heir.claim_once()
    assert again.id == item_id

    proceed.set()
    assert await stalled == "lease_lost"
    assert len(refused) == 1
    assert sorted(await work_db.effects()) == ["before", "nested"]


async def test_shutdown_releases_every_owned_item_without_charging_it(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    started = asyncio.Event()
    stubborn_started = asyncio.Event()

    @registry.handler(
        kind="demo.polite", cls=WorkClass.AUTOMATION, mode=WorkMode.JOB, payload=Demo
    )
    async def polite(ctx: JobContext, payload: Demo) -> None:
        started.set()
        await ctx.until_stopping()
        ctx.raise_if_stopping()  # the safe point

    @registry.handler(
        kind="demo.stubborn", cls=WorkClass.AUTOMATION, mode=WorkMode.JOB, payload=Demo
    )
    async def stubborn(ctx: JobContext, payload: Demo) -> None:
        stubborn_started.set()
        await asyncio.sleep(3600)

    polite_id = await work_db.enqueue(registry, "demo.polite")
    stubborn_id = await work_db.enqueue(registry, "demo.stubborn")
    runtime = WorkRuntime(
        work_db.sessions, classes=[WorkClass.AUTOMATION], registry=registry
    )
    await runtime.start()
    await asyncio.wait_for(asyncio.gather(started.wait(), stubborn_started.wait()), 10)
    await runtime.stop(grace_seconds=0.5)

    for item_id in (polite_id, stubborn_id):
        row = await work_db.item(item_id)
        assert row and row["state"] == "queued"
        assert row["attempts"] == 0 and row["lease_losses"] == 0
        assert row["lease_owner"] is None
    assert (
        len(
            await dispatcher_for(
                work_db.sessions, WorkClass.AUTOMATION, registry=registry
            ).claim_once()
        )
        == 2
    )


async def test_a_released_job_is_requeued_due_now(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.release", cls=WorkClass.AUTOMATION, mode=WorkMode.JOB, payload=Demo
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        raise Released()

    item_id = await work_db.enqueue(registry, "demo.release")
    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry
    )
    [item] = await dispatcher.claim_once()
    assert await dispatcher.execute(item) == "released"
    row = await work_db.item(item_id)
    assert row and row["state"] == "queued" and row["attempts"] == 0
    assert len(await dispatcher.claim_once()) == 1


async def test_a_failing_dead_hook_still_kills_the_item_and_says_so(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    async def broken_hook(ctx: DeadContext) -> None:
        await write_effect(ctx.uow.session, "half-written")
        raise RuntimeError("hook bug")

    @registry.handler(
        kind="demo.bad_hook",
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=Demo,
        retry=RetryPolicy(max_attempts=1),
        on_dead=broken_hook,
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        raise ItemFailure("x")

    item_id = await work_db.enqueue(registry, "demo.bad_hook")
    await run_until_idle(work_db.sessions, registry=registry)
    row = await work_db.item(item_id)
    assert row and row["state"] == "dead"
    assert row["error_class"] == "attempts_exhausted:on_dead_failed"
    assert await work_db.effects() == []


async def test_an_invalid_payload_goes_dead_at_once(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.typed", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        del ctx, payload

    item_id = await work_db.enqueue(registry, "demo.typed")
    await work_db.execute("UPDATE work_items SET payload = '{\"tag\": [1]}'")
    await run_until_idle(work_db.sessions, registry=registry)
    row = await work_db.item(item_id)
    assert row and row["state"] == "dead" and row["error_class"] == "invalid_payload"


async def test_a_connection_release_inside_a_handler_cannot_commit_the_fence(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    """Authorization commits a read-only session to hand its connection back.

    Reached inside a fenced transaction, that commit would make the handler's
    writes so far durable and drop the item's row lock -- half an effect that a
    replay then finds and skips. The item lock marks the session as holding
    state that dies at commit, so the release declines.
    """
    from app.core.infrastructure.db.transaction_locks import safe_to_release

    seen: list[bool] = []

    @registry.handler(
        kind="demo.fenced_fast", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def fast(ctx: FastContext, payload: Demo) -> None:
        seen.append(safe_to_release(ctx.uow.session))

    @registry.handler(
        kind="demo.fenced_job", cls=WorkClass.EVENTS, mode=WorkMode.JOB, payload=Demo
    )
    async def job(ctx: JobContext, payload: Demo) -> None:
        async with ctx.complete_work() as uow:
            seen.append(safe_to_release(uow.session))

    await work_db.enqueue(registry, "demo.fenced_fast")
    await work_db.enqueue(registry, "demo.fenced_job")
    assert await run_until_idle(work_db.sessions, registry=registry) == 2
    assert seen == [False, False]
