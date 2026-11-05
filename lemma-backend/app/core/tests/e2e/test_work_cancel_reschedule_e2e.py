"""Cancel, reschedule, time limits and per-class shutdown grace, against real Postgres.

Stop and wait-cancel delete a queued item in the caller's transaction; the
property that matters is that exactly one of "it ran" and "it was dropped"
happens, whatever a dispatcher is doing at the same moment, and that a running
item is never touched.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.infrastructure.work import config as work_config
from app.core.infrastructure.work.context import DeadContext, FastContext
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.runtime import WorkRuntime
from app.core.infrastructure.work.testing import dispatcher_for, run_until_idle
from app.core.infrastructure.work.types import WorkClass, WorkMode
from app.core.infrastructure.work.writer import (
    CancelOutcome,
    RescheduleOutcome,
    WorkWriter,
)
from app.core.tests.e2e.work_support import POD_A, POD_B, Demo, WorkDb, write_effect

pytestmark = [pytest.mark.e2e]

KIND = "demo.turn"


def _register_turn(registry: WorkRegistry) -> None:
    @registry.handler(kind=KIND, cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo)
    async def handle(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, f"ran:{payload.tag}", ctx.item.id)


async def _cancel(
    work_db: WorkDb, registry: WorkRegistry, key: str, ns: str = POD_A
) -> CancelOutcome:
    async with work_db.sessions() as session:
        outcome = await WorkWriter(session, registry=registry).cancel(KIND, ns, key)
        await session.commit()
    return outcome


async def _reschedule(
    work_db: WorkDb, registry: WorkRegistry, key: str, **when: object
) -> RescheduleOutcome:
    async with work_db.sessions() as session:
        outcome = await WorkWriter(session, registry=registry).reschedule(
            KIND,
            POD_A,
            key,
            **when,  # type: ignore[arg-type]
        )
        await session.commit()
    return outcome


async def _mark_running(work_db: WorkDb, key: str) -> None:
    await work_db.execute(
        "UPDATE work_items SET state = 'running', lease_owner = 'elsewhere', "
        "lease_until = now() + interval '60 seconds' WHERE dedup_key = :key",
        key=key,
    )


@pytest.mark.drives_work
async def test_cancel_drops_a_queued_item_due_or_not_and_it_never_runs(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    await work_db.enqueue(registry, KIND, payload=Demo(tag="now"), dedup_key="k:now")
    await work_db.enqueue(
        registry,
        KIND,
        payload=Demo(tag="later"),
        dedup_key="k:later",
        delay=timedelta(hours=1),
    )

    assert await _cancel(work_db, registry, "k:now") == "dropped"
    assert await _cancel(work_db, registry, "k:later") == "dropped"
    assert await _cancel(work_db, registry, "k:now") == "absent"
    assert await _cancel(work_db, registry, "never-written") == "absent"

    assert await run_until_idle(work_db.sessions, registry=registry) == 0
    assert await work_db.count() == 0
    assert await work_db.effects() == []


async def test_cancel_only_touches_its_own_kind_and_namespace(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    await work_db.enqueue(registry, KIND, POD_B, Demo(tag="b"), dedup_key="k:1")

    assert await _cancel(work_db, registry, "k:1", ns=POD_A) == "absent"
    assert await work_db.count() == 1


@pytest.mark.drives_work
async def test_a_rolled_back_cancel_leaves_the_item_to_run(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    await work_db.enqueue(registry, KIND, payload=Demo(tag="x"), dedup_key="k:1")

    async with work_db.sessions() as session:
        writer = WorkWriter(session, registry=registry)
        assert await writer.cancel(KIND, POD_A, "k:1") == "dropped"
        await session.rollback()

    assert await run_until_idle(work_db.sessions, registry=registry) == 1
    assert await work_db.effects() == ["ran:x"]


@pytest.mark.drives_work
async def test_cancel_while_running_leaves_the_running_item_alone(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    @registry.handler(
        kind=KIND, cls=WorkClass.AUTOMATION, mode=WorkMode.JOB, payload=Demo
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        started.set()
        await release.wait()
        async with ctx.complete_work() as uow:
            await write_effect(uow.session, "finished", ctx.item.id)

    await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")
    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry
    )
    [item] = await dispatcher.claim_once()
    task = asyncio.create_task(dispatcher.execute(item))
    await asyncio.wait_for(started.wait(), timeout=5)

    async with work_db.sessions() as session:
        outcome = await WorkWriter(session, registry=registry).cancel(
            KIND, POD_A, "k:1"
        )
        await session.commit()
    assert outcome == "running"
    assert await work_db.count("state = 'running'") == 1

    release.set()
    assert await asyncio.wait_for(task, timeout=5) == "done"
    assert await work_db.effects() == ["finished"]
    assert await work_db.count() == 0


async def test_cancel_with_a_running_item_and_a_queued_twin_drops_only_the_twin(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    running = await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")
    await _mark_running(work_db, "k:1")
    twin = await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")
    assert twin is not None and twin != running

    assert await _cancel(work_db, registry, "k:1") == "running"
    assert await work_db.item(twin) is None
    assert running is not None
    row = await work_db.item(running)
    assert row is not None and row["state"] == "running"


@pytest.mark.drives_work
async def test_a_cancel_holding_the_row_makes_a_concurrent_claim_skip_it(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")
    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)

    async with work_db.sessions() as session:
        writer = WorkWriter(session, registry=registry)
        assert await writer.cancel(KIND, POD_A, "k:1") == "dropped"
        # Uncommitted: the claim must neither wait on the row nor take it.
        assert await asyncio.wait_for(dispatcher.claim_once(), timeout=5) == []
        await session.commit()

    assert await work_db.count() == 0
    assert await run_until_idle(work_db.sessions, registry=registry) == 0


async def test_a_cancel_behind_a_claim_holding_the_row_sees_it_running(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    item_id = await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")

    async with work_db.sessions() as claimer:
        # What a claim does, held open: lock the row, mark it running.
        await claimer.execute(
            text("SELECT 1 FROM work_items WHERE id = :id FOR UPDATE"), {"id": item_id}
        )
        await claimer.execute(
            text(
                "UPDATE work_items SET state = 'running', lease_owner = 'w', "
                "lease_until = now() + interval '60 seconds' WHERE id = :id"
            ),
            {"id": item_id},
        )
        cancel = asyncio.create_task(_cancel(work_db, registry, "k:1"))
        await asyncio.sleep(0.3)
        assert not cancel.done(), "the delete must wait for the claim's row lock"
        await claimer.commit()

    assert await asyncio.wait_for(cancel, timeout=5) == "running"
    row = await work_db.item(item_id)
    assert row is not None and row["state"] == "running"


@pytest.mark.drives_work
@pytest.mark.parametrize("round_", range(30))
async def test_cancel_racing_a_dispatcher_ends_in_exactly_one_outcome(
    work_db: WorkDb, registry: WorkRegistry, round_: int
) -> None:
    _register_turn(registry)
    await work_db.enqueue(registry, KIND, payload=Demo(tag="r"), dedup_key="k:race")
    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)

    async def claim_and_run() -> int:
        items = await dispatcher.claim_once()
        for item in items:
            await dispatcher.execute(item)
        return len(items)

    # Stagger by round so both orders (and the overlap) are exercised.
    async def cancel_after_a_beat() -> CancelOutcome:
        await asyncio.sleep((round_ % 5) * 0.002)
        return await _cancel(work_db, registry, "k:race")

    ran, outcome = await asyncio.gather(claim_and_run(), cancel_after_a_beat())
    effects = await work_db.effects()
    if outcome == "dropped":
        assert ran == 0 and effects == []
    else:
        # The claim won: the item ran exactly once (it may already be gone).
        assert outcome in ("running", "absent")
        assert ran == 1 and effects == ["ran:r"]
    assert await work_db.count() == 0


@pytest.mark.drives_work
async def test_cancel_matching_drops_queued_items_by_payload(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    for tag in ("a", "a", "b"):
        await work_db.enqueue(registry, KIND, payload=Demo(tag=tag))
    await work_db.enqueue(registry, KIND, POD_B, Demo(tag="a"))

    async with work_db.sessions() as session:
        dropped = await WorkWriter(session, registry=registry).cancel_matching(
            KIND, POD_A, {"tag": "a"}
        )
        await session.commit()

    assert dropped == 2
    await run_until_idle(work_db.sessions, registry=registry)
    assert sorted(await work_db.effects()) == ["ran:a", "ran:b"]


@pytest.mark.drives_work
async def test_reschedule_later_holds_an_item_back(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    item_id = await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")

    outcome = await _reschedule(work_db, registry, "k:1", delay=timedelta(hours=1))

    assert outcome == "moved"
    assert await run_until_idle(work_db.sessions, registry=registry) == 0
    assert item_id is not None
    row = await work_db.item(item_id)
    assert row is not None and row["run_at"] > datetime.now(UTC) + timedelta(minutes=59)


@pytest.mark.drives_work
async def test_reschedule_earlier_moves_an_item_ahead_of_older_work(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    await work_db.enqueue(
        registry,
        KIND,
        payload=Demo(tag="old"),
        run_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    await work_db.enqueue(
        registry,
        KIND,
        payload=Demo(tag="new"),
        dedup_key="k:new",
        delay=timedelta(hours=1),
    )

    outcome = await _reschedule(
        work_db, registry, "k:new", run_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    assert outcome == "moved"

    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.EVENTS, registry=registry, slots=1
    )
    [first] = await dispatcher.claim_once()
    assert first.payload == {"tag": "new"}


async def test_reschedule_reports_running_and_absent_and_keeps_budgets(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _register_turn(registry)
    assert await _reschedule(work_db, registry, "missing") == "absent"

    item_id = await work_db.enqueue(registry, KIND, payload=Demo(), dedup_key="k:1")
    await work_db.execute(
        "UPDATE work_items SET attempts = 2, lease_losses = 1 WHERE id = :id",
        id=item_id,
    )
    assert await _reschedule(work_db, registry, "k:1", delay=timedelta(minutes=5)) == (
        "moved"
    )
    assert item_id is not None
    row = await work_db.item(item_id)
    assert row is not None and (row["attempts"], row["lease_losses"]) == (2, 1)

    await _mark_running(work_db, "k:1")
    assert await _reschedule(work_db, registry, "k:1") == "running"


async def _record_dead(ctx: DeadContext) -> None:
    await write_effect(ctx.uow.session, f"terminal:{ctx.reason}", ctx.item.id)


@pytest.mark.drives_work
async def test_a_job_past_its_max_runtime_is_cancelled_and_goes_dead(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    deadlines: list[datetime | None] = []
    cancelled = asyncio.Event()

    @registry.handler(
        kind="demo.slow",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        max_runtime=timedelta(milliseconds=500),
        on_dead=_record_dead,
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        deadlines.append(ctx.deadline)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    item_id = await work_db.enqueue(registry, "demo.slow")
    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry
    )
    [item] = await dispatcher.claim_once()

    outcome = await asyncio.wait_for(dispatcher.execute(item), timeout=10)

    assert outcome == "dead"
    assert cancelled.is_set()
    assert deadlines[0] is not None
    assert item_id is not None
    row = await work_db.item(item_id)
    assert row is not None
    assert row["state"] == "dead" and row["error_class"] == "max_runtime"
    assert await work_db.effects() == ["terminal:max_runtime"]


@pytest.mark.drives_work
async def test_a_job_inside_its_max_runtime_completes_normally(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.quick",
        cls=WorkClass.AUTOMATION,
        mode=WorkMode.JOB,
        payload=Demo,
        max_runtime=timedelta(seconds=10),
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        async with ctx.complete_work() as uow:
            await write_effect(uow.session, "done", ctx.item.id)

    await work_db.enqueue(registry, "demo.quick")
    assert await run_until_idle(work_db.sessions, registry=registry) == 1
    assert await work_db.effects() == ["done"]
    assert await work_db.count() == 0


async def test_shutdown_gives_each_class_its_own_grace(
    work_db: WorkDb, registry: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        work_config.work_settings,
        "work_shutdown_grace_json",
        '{"interactive": 5}',
    )
    monkeypatch.setattr(work_config.work_settings, "work_shutdown_grace_seconds", 0.2)
    started = {
        WorkClass.INTERACTIVE: asyncio.Event(),
        WorkClass.AUTOMATION: asyncio.Event(),
    }

    def register(kind: str, cls: WorkClass) -> None:
        @registry.handler(kind=kind, cls=cls, mode=WorkMode.JOB, payload=Demo)
        async def handle(ctx: JobContext, payload: Demo) -> None:
            started[cls].set()
            await asyncio.sleep(1.0)  # past the short grace, inside the long one
            async with ctx.complete_work() as uow:
                await write_effect(uow.session, f"finished:{cls.value}", ctx.item.id)

    register("demo.chat", WorkClass.INTERACTIVE)
    register("demo.batch", WorkClass.AUTOMATION)
    await work_db.enqueue(registry, "demo.chat")
    batch_id = await work_db.enqueue(registry, "demo.batch")

    runtime = WorkRuntime(
        work_db.sessions,
        classes=[WorkClass.INTERACTIVE, WorkClass.AUTOMATION],
        registry=registry,
    )
    await runtime.start()
    await asyncio.wait_for(
        asyncio.gather(*(event.wait() for event in started.values())), timeout=10
    )
    await runtime.stop()

    assert await work_db.effects() == ["finished:interactive"]
    assert batch_id is not None
    row = await work_db.item(batch_id)
    # Released, not charged: the next process picks it up at once.
    assert row is not None
    assert (row["state"], row["attempts"], row["lease_losses"]) == ("queued", 0, 0)
