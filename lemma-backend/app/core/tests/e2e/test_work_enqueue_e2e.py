"""Enqueue against real Postgres: transactional, deduplicated only while queued, fanned out.

What only the database can answer: a rolled-back enqueue leaves nothing, the
partial unique index coalesces queued twins but not a running one, and a
periodic armed twice is still one chain.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.facts import Fact, FactBus
from app.core.infrastructure.work.limits import ConfiguredWorkLimitPort
from app.core.infrastructure.work.maintenance import arm_periodics, refresh_queues
from app.core.infrastructure.work.registry import UnknownWorkKind, WorkRegistry
from app.core.infrastructure.work.testing import (
    dispatcher_for,
    make_due,
    run_until_idle,
)
from app.core.infrastructure.work.types import SYSTEM_NAMESPACE, WorkClass, WorkMode
from app.core.infrastructure.work.writer import WorkWriter
from app.modules.test_support.e2e.waiters import eventually
from app.core.tests.e2e.work_support import (
    POD_A,
    POD_B,
    Demo,
    WorkDb,
    write_effect,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _fast(registry: WorkRegistry, kind: str = "demo.fast", **options: object) -> None:
    @registry.handler(
        kind=kind, cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo, **options
    )  # type: ignore[call-overload]
    async def handle(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, payload.tag or kind, ctx.item.id)


async def test_an_enqueue_rolled_back_with_its_transaction_leaves_nothing(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    async with work_db.sessions() as session:
        await write_effect(session, "state")
        await WorkWriter(session, registry=registry).add(
            "demo.fast", POD_A, Demo(tag="x")
        )
        await session.rollback()
    assert await work_db.count() == 0
    assert await work_db.effects() == []

    async with work_db.sessions() as session:
        await write_effect(session, "state")
        await WorkWriter(session, registry=registry).add(
            "demo.fast", POD_A, Demo(tag="x")
        )
        await session.commit()
    assert await work_db.count("state = 'queued'") == 1
    assert await work_db.scalar("SELECT count(*) FROM work_queues") == 1


async def test_dedup_coalesces_queued_items_but_a_wake_while_running_is_kept(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    first = await work_db.enqueue(registry, "demo.fast", dedup_key="k")
    again = await work_db.enqueue(registry, "demo.fast", dedup_key="k")
    assert first is not None and again is None
    assert await work_db.count() == 1

    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)
    claimed = await dispatcher.claim_once()
    assert [item.id for item in claimed] == [first]

    # The first is running: this wake must produce new work or it is lost.
    during = await work_db.enqueue(registry, "demo.fast", dedup_key="k")
    assert during is not None
    assert await work_db.count("state = 'running'") == 1
    assert await work_db.count("state = 'queued'") == 1

    await dispatcher.execute(claimed[0])
    assert await run_until_idle(work_db.sessions, registry=registry) == 1
    assert len(await work_db.effects()) == 2


async def test_the_writer_refuses_unknown_kinds_bad_namespaces_and_bad_payloads(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    async with work_db.sessions() as session:
        writer = WorkWriter(session, registry=registry)
        with pytest.raises(UnknownWorkKind):
            await writer.add("nope", POD_A)
        with pytest.raises(ValueError, match="namespace"):
            await writer.add("demo.fast", "pod:not-a-uuid")
        with pytest.raises(ValueError, match="concurrency"):
            await writer.add("demo.fast", POD_A, concurrency_key="c")
        with pytest.raises(ValueError):
            await writer.add("demo.fast", POD_A, {"tag": {"not": "a string"}})


async def test_a_new_queue_row_takes_its_cap_from_the_limit_port_and_refresh_follows(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    pod_id = POD_B.removeprefix("pod:")
    limits = ConfiguredWorkLimitPort(
        raw_overrides=f'[{{"pod_id": "{pod_id}", "max_running": {{"events": 3}}}}]'
    )
    async with work_db.sessions() as session:
        await WorkWriter(session, registry=registry, limits=limits).add(
            "demo.fast", POD_B
        )
        await session.commit()
    cap = "SELECT max_running FROM work_queues WHERE ns = :ns"
    assert await work_db.scalar(cap, ns=POD_B) == 3

    changed = ConfiguredWorkLimitPort(
        raw_overrides=f'[{{"pod_id": "{pod_id}", "max_running": {{"events": 5}}}}]'
    )
    async with work_db.sessions() as session:
        assert await refresh_queues(session, WorkClass.EVENTS, changed) == 1
        await session.commit()
    assert await work_db.scalar(cap, ns=POD_B) == 5


async def test_a_queue_row_lost_under_an_uncommitted_enqueue_is_repaired(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    await work_db.enqueue(registry, "demo.fast", POD_A)
    await work_db.execute("DELETE FROM work_queues")
    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)
    assert await dispatcher.claim_once() == []

    async with work_db.sessions() as session:
        await refresh_queues(
            session, WorkClass.EVENTS, ConfiguredWorkLimitPort(raw_overrides="")
        )
        await session.commit()
    assert len(await dispatcher.claim_once()) == 1


async def test_periodic_arming_is_idempotent_and_each_run_rearms_the_next(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry, "demo.tick")
    registry.periodic("tick", timedelta(minutes=5), "demo.tick")
    classes = [WorkClass.EVENTS]

    for _ in range(3):
        async with work_db.sessions() as session:
            await arm_periodics(session, registry, classes)
            await session.commit()
    assert await work_db.count("dedup_key = 'periodic:tick'") == 1

    assert await run_until_idle(work_db.sessions, registry=registry) == 1
    next_run = await work_db.scalar(
        "SELECT run_at - now() FROM work_items WHERE dedup_key = 'periodic:tick' "
        "AND state = 'queued'"
    )
    assert isinstance(next_run, timedelta)
    assert timedelta(minutes=4) < next_run <= timedelta(minutes=5)
    assert (
        await work_db.scalar(
            "SELECT ns FROM work_items WHERE dedup_key = 'periodic:tick'"
        )
        == SYSTEM_NAMESPACE
    )

    # Running: arming again must not start a second chain.
    await make_due(work_db.sessions)
    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)
    claimed = await dispatcher.claim_once()
    async with work_db.sessions() as session:
        await arm_periodics(session, registry, classes)
        await session.commit()
    assert await work_db.count("dedup_key = 'periodic:tick'") == 1
    await dispatcher.execute(claimed[0])
    assert await work_db.count("dedup_key = 'periodic:tick' AND state = 'queued'") == 1


async def test_a_dead_periodic_is_recreated_at_the_next_boot(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry, "demo.tick")
    registry.periodic("tick", timedelta(hours=1), "demo.tick")
    async with work_db.sessions() as session:
        await arm_periodics(session, registry, [WorkClass.EVENTS])
        await session.commit()
    await work_db.execute("UPDATE work_items SET state = 'dead'")
    async with work_db.sessions() as session:
        assert await arm_periodics(session, registry, [WorkClass.EVENTS]) == 1
        await session.commit()
    assert await work_db.count("state = 'queued'") == 1
    assert await work_db.count("state = 'dead'") == 1


class _Finished(Fact):
    run_id: str
    pod: str
    parent_kind: str

    def work_namespace(self) -> str:
        return self.pod


async def test_facts_write_one_item_per_matching_subscriber_in_the_emitting_transaction(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    for kind in ("on.workflow", "on.schedule", "on.audit"):

        @registry.handler(
            kind=kind, cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=_Finished
        )
        async def handle(ctx: FastContext, fact: _Finished) -> None:
            await write_effect(ctx.uow.session, f"{ctx.item.kind}:{fact.run_id}")

    bus = FactBus(registry)
    bus.subscribe(
        _Finished,
        kind="on.workflow",
        cls=WorkClass.EVENTS,
        when=lambda f: f.parent_kind == "workflow",
        dedup=lambda f: f"resume:{f.run_id}",
    )
    bus.subscribe(
        _Finished, kind="on.schedule", when=lambda f: f.parent_kind == "schedule"
    )
    bus.subscribe(_Finished, kind="on.audit")

    fact = _Finished(run_id="r1", pod=POD_A, parent_kind="workflow")
    async with work_db.sessions() as session:
        holder = _SessionHolder(session)
        assert await bus.emit(holder, fact) == 2
        assert await bus.emit(holder, fact) == 1  # the dedup'd one coalesces
        await session.rollback()
    assert await work_db.count() == 0

    async with work_db.sessions() as session:
        await bus.emit(_SessionHolder(session), fact)
        await session.commit()
    assert await run_until_idle(work_db.sessions, registry=registry) == 2
    assert sorted(await work_db.effects()) == ["on.audit:r1", "on.workflow:r1"]


async def test_a_subscriber_declared_in_the_wrong_class_is_refused_at_emit(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="on.x", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=_Finished
    )
    async def handle(ctx: FastContext, fact: _Finished) -> None:
        del ctx, fact

    bus = FactBus(registry)
    bus.subscribe(_Finished, kind="on.x", cls=WorkClass.DELIVERY)
    async with work_db.sessions() as session:
        with pytest.raises(ValueError, match="registered in events"):
            await bus.emit(
                _SessionHolder(session),
                _Finished(run_id="r", pod=POD_A, parent_kind=""),
            )


class _SessionHolder:
    def __init__(self, session: object) -> None:
        self.session = session


async def test_notify_is_sent_on_the_class_channel_at_commit_only(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    import asyncio

    import asyncpg

    from app.core.infrastructure.events.outbox_wake import asyncpg_connect_kwargs

    url = work_db.engine.url.render_as_string(hide_password=False)
    listener = await asyncpg.connect(
        **asyncpg_connect_kwargs(url, application_name="t")
    )
    received: list[str] = []
    try:
        await listener.add_listener(
            "work_events", lambda *args: received.append(args[2])
        )
        async with work_db.sessions() as session:
            await WorkWriter(session, registry=registry).add("demo.fast", POD_A)
            await session.rollback()
        await work_db.enqueue(registry, "demo.fast", POD_A)
        await work_db.enqueue(registry, "demo.fast", POD_A)

        async def count() -> int:
            return len(received)

        await eventually(
            label="two notifications",
            probe=count,
            done=lambda n: n >= 2,
            timeout_seconds=5,
            interval_seconds=0.05,
        )
        await asyncio.sleep(0.2)  # room for a wrong third notification to show up
    finally:
        await listener.close()
    assert received == ["work_events", "work_events"]


async def test_work_a_handler_enqueues_wakes_its_own_process_not_the_database(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    """A handler's follow-up wakes the local dispatcher after commit; no NOTIFY.

    NOTIFY serialises every notifying commit database-wide, so a burst of
    handler commits (5,000 schedule fires) paid it once each. The API's
    enqueue still notifies: nobody else would look before the poll.
    """
    import asyncpg

    from app.core.infrastructure.events.outbox_wake import asyncpg_connect_kwargs

    _fast(registry)

    @registry.handler(
        kind="demo.chain", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def chain(ctx: FastContext, payload: Demo) -> None:
        await ctx.work.add("demo.fast", POD_B, Demo(tag="follow-up"))

    url = work_db.engine.url.render_as_string(hide_password=False)
    listener = await asyncpg.connect(
        **asyncpg_connect_kwargs(url, application_name="t")
    )
    received: list[str] = []

    async def seen() -> list[str]:
        return list(received)

    async def until(payload: str) -> None:
        await eventually(
            label=f"notification {payload!r}",
            probe=seen,
            done=lambda seen: payload in seen,
            timeout_seconds=5,
            interval_seconds=0.02,
        )

    try:
        await listener.add_listener(
            "work_events", lambda *args: received.append(args[3])
        )
        await work_db.enqueue(registry, "demo.chain", POD_A)
        await until("")  # the API-side enqueue notifies

        woken: list[WorkClass] = []
        dispatcher = dispatcher_for(
            work_db.sessions, WorkClass.EVENTS, registry=registry
        )
        dispatcher.env.local_wake = woken.append
        claimed = await dispatcher.claim_once()
        assert [item.kind for item in claimed] == ["demo.chain"]
        assert await dispatcher.execute(claimed[0]) == "done"

        # Notifications arrive in commit order: had the handler's commit
        # notified, it would be seen before this marker.
        async with work_db.sessions() as session:
            await session.execute(text("SELECT pg_notify('work_events', 'marker')"))
            await session.commit()
        await until("marker")
    finally:
        await listener.close()

    assert received == ["", "marker"]
    assert woken == [WorkClass.EVENTS]
    assert await work_db.count("state = 'queued' AND ns = :ns", ns=POD_B) == 1


async def test_payloads_are_stored_as_ids_shaped_json(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    _fast(registry)
    item_id = await work_db.enqueue(registry, "demo.fast", POD_A, Demo(tag="t"))
    async with work_db.sessions() as session:
        payload = await session.scalar(
            text("SELECT payload FROM work_items WHERE id = :id"), {"id": item_id}
        )
    assert payload == {"tag": "t"}


async def test_two_writers_creating_new_namespaces_in_opposite_orders_do_not_deadlock(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    """Each holds its first new namespace while asking for the other's.

    With the queue row inserted inside the business transaction this is a
    textbook deadlock; Postgres aborts one of the two writers.
    """
    _fast(registry)
    first_done = [asyncio.Event(), asyncio.Event()]

    async def writer(index: int, order: list[str]) -> None:
        async with work_db.sessions() as session:
            work = WorkWriter(session, registry=registry)
            await work.add("demo.fast", order[0])
            first_done[index].set()
            await first_done[1 - index].wait()
            await asyncio.wait_for(work.add("demo.fast", order[1]), timeout=10)
            await session.commit()

    await asyncio.gather(writer(0, [POD_A, POD_B]), writer(1, [POD_B, POD_A]))
    assert await work_db.count() == 4
    assert await work_db.scalar("SELECT count(*) FROM work_queues") == 2
