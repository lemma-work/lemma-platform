"""The fair claim against real Postgres: fairness, caps, pause, and concurrency keys under races.

The concurrency-key test runs several dispatchers at once, each on its own
connections, so their claim transactions genuinely interleave in the server;
the in-process counter only observes overlap, it does not prevent it.
"""

from __future__ import annotations

import asyncio
import random
from collections import Counter, defaultdict
from uuid import UUID

import pytest
from sqlalchemy import text

from app.core.infrastructure.work import claim as claim_sql
from app.core.infrastructure.work.admin import set_paused
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.dispatcher import Dispatcher
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.limits import ConfiguredWorkLimitPort
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.testing import dispatcher_for
from app.core.infrastructure.work.types import WorkClass, WorkMode
from app.core.infrastructure.work.writer import WorkWriter
from app.modules.test_support.e2e.waiters import eventually
from app.core.tests.e2e.work_support import (
    POD_A,
    POD_B,
    Demo,
    WorkDb,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _bulk(work_db: WorkDb, ns: str, count: int, *, kind: str, age: float) -> None:
    """Load a backlog in one statement; enqueueing 10k one by one tests nothing extra."""
    await work_db.execute(
        "INSERT INTO work_items (id, ns, class, kind, payload, run_at) "
        "SELECT gen_random_uuid(), :ns, 'events', :kind, '{}', now() - make_interval(secs => :age) "
        "FROM generate_series(1, :count)",
        ns=ns,
        kind=kind,
        age=age,
        count=count,
    )
    await work_db.execute(
        "INSERT INTO work_queues (class, ns, max_running) VALUES ('events', :ns, 16) "
        "ON CONFLICT DO NOTHING",
        ns=ns,
    )


async def test_a_quiet_pod_is_served_within_one_round_behind_a_10k_backlog(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    served: list[str] = []

    @registry.handler(
        kind="demo.fast", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        served.append(ctx.item.ns)

    # A's backlog is older than anything B has, the worst case for B under FIFO.
    await _bulk(work_db, POD_A, 10_000, kind="demo.fast", age=3600.0)
    for _ in range(10):
        await work_db.enqueue(registry, "demo.fast", POD_B)

    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.EVENTS, registry=registry, slots=4
    )
    rounds = 0
    first_b_round = None
    while served.count(POD_B) < 10:
        rounds += 1
        items = await dispatcher.claim_once()
        if first_b_round is None and any(i.ns == POD_B for i in items):
            first_b_round = rounds
        await asyncio.gather(*(dispatcher.execute(item) for item in items))
        assert rounds < 50

    a_before_b_done = served.count(POD_A)
    print(
        f"fairness: B first claimed in round {first_b_round}, B done after "
        f"{rounds} rounds, {a_before_b_done} A items served meanwhile"
    )
    assert first_b_round == 1
    # Two namespaces split four slots evenly: B's ten take five rounds, and A
    # gets exactly the other half of every one of them.
    assert rounds == 5
    assert a_before_b_done == 10


async def test_a_pod_alone_fills_every_slot_it_has_room_for_in_one_round(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.fast", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        del ctx, payload

    await _bulk(work_db, POD_A, 100, kind="demo.fast", age=1.0)
    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.EVENTS, registry=registry, slots=32
    )
    claimed = await dispatcher.claim_once()
    assert len(claimed) == 16  # the events per-pod cap, not the quantum of 2


async def test_the_per_namespace_cap_holds_and_frees_as_work_finishes(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    gate = asyncio.Event()

    @registry.handler(
        kind="demo.job", cls=WorkClass.AUTOMATION, mode=WorkMode.JOB, payload=Demo
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        await gate.wait()

    pod_id = POD_A.removeprefix("pod:")
    limits = ConfiguredWorkLimitPort(
        raw_overrides=f'[{{"pod_id": "{pod_id}", "max_running": {{"automation": 3}}}}]'
    )
    async with work_db.sessions() as session:
        writer = WorkWriter(session, registry=registry, limits=limits)
        for _ in range(8):
            await writer.add("demo.job", POD_A)
        await session.commit()

    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, slots=10
    )
    first = await dispatcher.claim_once()
    assert len(first) == 3
    running = [asyncio.create_task(dispatcher.execute(item)) for item in first]
    assert await dispatcher.claim_once() == []
    assert await work_db.count("state = 'running'") == 3

    gate.set()
    assert set(await asyncio.gather(*running)) == {"done"}
    assert len(await dispatcher.claim_once()) == 3


async def test_a_paused_namespace_waits_and_resumes(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.fast", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        del ctx, payload

    await work_db.enqueue(registry, "demo.fast", POD_A)
    await work_db.enqueue(registry, "demo.fast", POD_B)
    async with work_db.sessions() as session:
        await set_paused(session, POD_A, paused=True, cls=WorkClass.EVENTS)
        await session.commit()
    dispatcher = dispatcher_for(work_db.sessions, WorkClass.EVENTS, registry=registry)
    assert [i.ns for i in await dispatcher.claim_once()] == [POD_B]
    assert await dispatcher.claim_once() == []

    async with work_db.sessions() as session:
        await set_paused(session, POD_A, paused=False, cls=None)
        await session.commit()
    assert [i.ns for i in await dispatcher.claim_once()] == [POD_A]


async def test_a_busy_key_is_skipped_and_keyed_work_keeps_its_place(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    @registry.handler(
        kind="demo.keyed",
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=Demo,
        concurrency=1,
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        del ctx, payload

    first = await work_db.enqueue(registry, "demo.keyed", concurrency_key="conv:1")
    second = await work_db.enqueue(registry, "demo.keyed", concurrency_key="conv:1")
    unkeyed = [await work_db.enqueue(registry, "demo.keyed") for _ in range(3)]
    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.INTERACTIVE, registry=registry
    )

    round_one = {i.id for i in await dispatcher.claim_once()}
    assert first in round_one and second not in round_one
    assert set(unkeyed) <= round_one

    # Later unkeyed work is not held behind the busy key...
    third = await work_db.enqueue(registry, "demo.keyed")
    round_two = {i.id for i in await dispatcher.claim_once()}
    assert round_two == {third}
    # ...and the keyed item is next the moment its key frees.
    await work_db.execute("DELETE FROM work_items WHERE id = :id", id=first)
    assert [i.id for i in await dispatcher.claim_once()] == [second]


async def _race(
    work_db: WorkDb,
    registry: WorkRegistry,
    *,
    seed: int,
    dispatchers: int,
    keys: int,
    limit: int,
    per_key: int,
    unkeyed: int,
) -> tuple[dict[str, int], Counter[UUID]]:
    rng = random.Random(seed)
    running: dict[str, int] = defaultdict(int)
    peak: dict[str, int] = defaultdict(int)
    handled: Counter[UUID] = Counter()
    kind = f"demo.race{limit}"

    @registry.handler(
        kind=kind,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=Demo,
        concurrency=limit,
    )
    async def handle(ctx: JobContext, payload: Demo) -> None:
        key = payload.tag
        running[key] += 1
        peak[key] = max(peak[key], running[key])
        await asyncio.sleep(rng.uniform(0, 0.02))
        running[key] -= 1
        handled[ctx.item.id] += 1

    async with work_db.sessions() as session:
        writer = WorkWriter(session, registry=registry)
        jobs = [
            (f"conv:{k}", Demo(tag=f"conv:{k}"))
            for k in range(keys)
            for _ in range(per_key)
        ]
        jobs += [(None, Demo(tag="none")) for _ in range(unkeyed)]
        rng.shuffle(jobs)
        for index, (key, payload) in enumerate(jobs):
            ns = POD_A if index % 2 else POD_B
            await writer.add(kind, ns, payload, concurrency_key=key)
        await session.commit()

    workers: list[Dispatcher] = [
        dispatcher_for(
            work_db.sessions,
            WorkClass.INTERACTIVE,
            registry=registry,
            owner=f"d{i}-{seed}",
            slots=rng.randint(2, 8),
            quantum=rng.randint(1, 3),
        )
        for i in range(dispatchers)
    ]
    loops = [asyncio.create_task(d.run()) for d in workers]
    await eventually(
        label="race drained",
        probe=work_db.count,
        done=lambda left: left == 0,
        timeout_seconds=30,
        interval_seconds=0.05,
    )
    for d in workers:
        d.env.stopping.set()
        d.wake()
    await asyncio.gather(*loops)
    await asyncio.gather(*(d.drain(5) for d in workers))
    peak.pop("none", None)
    return dict(peak), handled


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_a_key_with_limit_one_never_runs_twice_across_racing_dispatchers(
    work_db: WorkDb, registry: WorkRegistry, seed: int
) -> None:
    peak, handled = await _race(
        work_db,
        registry,
        seed=seed,
        dispatchers=4,
        keys=4,
        limit=1,
        per_key=25,
        unkeyed=40,
    )
    assert await work_db.count() == 0
    assert len(handled) == 140 and set(handled.values()) == {1}
    assert peak and max(peak.values()) == 1


async def test_a_key_with_limit_two_is_used_up_to_two_and_never_past_it(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    peak, handled = await _race(
        work_db, registry, seed=7, dispatchers=3, keys=2, limit=2, per_key=30, unkeyed=0
    )
    assert len(handled) == 60 and set(handled.values()) == {1}
    assert max(peak.values()) == 2


def _literal(statement: str, **params: object) -> str:
    """Inline parameters so EXPLAIN plans the query as the claim runs it."""
    for name, value in params.items():
        rendered = (
            "ARRAY[" + ",".join(f"'{v}'" for v in value) + "]"  # type: ignore[attr-defined]
            if isinstance(value, list)
            else f"'{value}'"
            if isinstance(value, str)
            else str(value)
        )
        statement = statement.replace(f":{name}", rendered)
    return statement


async def test_the_claim_queries_use_the_partial_indexes(
    work_db: WorkDb, registry: WorkRegistry
) -> None:
    for n in range(200):
        ns = f"pod:{n:08x}-0000-0000-0000-000000000000"
        await _bulk(work_db, ns, 50, kind="demo.fast", age=60.0)
    await work_db.execute("ANALYZE work_items")
    await work_db.execute("ANALYZE work_queues")

    plans = {}
    async with work_db.sessions() as session:
        for name, statement, params in (
            (
                "ready",
                claim_sql._READY_QUEUES,
                {"cls": "events", "kinds": ["demo.fast"], "limit": 32},
            ),
            (
                "candidates",
                claim_sql._CANDIDATES,
                {"cls": "events", "ns": POD_A, "kinds": ["demo.fast"], "limit": 8},
            ),
        ):
            sql = "EXPLAIN " + _literal(statement.text, **params)
            plans[name] = "\n".join(r[0] for r in await session.execute(text(sql)))
    print(plans["ready"], plans["candidates"], sep="\n---\n")
    assert "work_queues_served" in plans["ready"]
    # Readiness must stay a per-queue probe: a flattened semi-join reads the
    # whole backlog on every claim.
    assert "Semi Join" not in plans["ready"] and "HashAggregate" not in plans["ready"]
    assert "work_items_ready" in plans["ready"]
    assert "work_items_running" in plans["ready"]
    assert "work_items_ready" in plans["candidates"]
