"""Work runtime benchmark: enqueue throughput, claim cost, and latency under skewed load.

Not part of any test lane. Point it at a disposable Postgres -- it drops and
recreates ``work_items``, ``work_queues`` and ``bench_effects``:

    uv run python load_tests/work_runtime_bench.py --database-url postgresql+asyncpg://...

Phases:

1. enqueue -- concurrent writers, one item per business transaction (the
   realistic shape) and ten per transaction.
2. drain -- a skewed backlog over many namespaces, drained by several dispatcher
   processes; reports throughput and the claim transaction's duration.
3. steady -- producers at a fixed rate with two hot namespaces carrying most of
   the load, dispatchers running; reports due-to-start latency for hot and quiet
   namespaces separately. That split is the fairness claim in numbers.
4. explain -- EXPLAIN (ANALYZE, BUFFERS) of the claim's two queries on a large
   backlog, to show they ride the partial indexes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import random
import time

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.infrastructure.work import claim as claim_sql
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.dispatcher import Dispatcher
from app.core.infrastructure.work.db import DbBudget, WorkDb
from app.core.infrastructure.work.execute import ExecEnv
from app.core.infrastructure.work.listener import WorkWakeListener
from app.core.infrastructure.work.models import WorkItem, WorkQueue, WorkRateBucket
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.types import ClaimedItem, WorkClass, WorkMode
from app.core.infrastructure.work.writer import WorkWriter

REGISTRY = WorkRegistry()


class Empty(BaseModel):
    pass


@REGISTRY.handler(
    kind="bench.noop", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Empty
)
async def bench_noop(ctx: FastContext, payload: Empty) -> None:
    """One small write, like a real reaction; records due-to-start latency by DB clock."""
    await ctx.uow.session.execute(
        text(
            "INSERT INTO bench_effects (ns, latency) "
            "VALUES (:ns, EXTRACT(EPOCH FROM clock_timestamp() - :run_at))"
        ),
        {"ns": ctx.item.ns, "run_at": ctx.item.run_at},
    )


def namespace(n: int) -> str:
    return f"pod:{n:08x}-0000-4000-8000-000000000000"


def pick_namespace(rng: random.Random, namespaces: int, hot_share: float) -> str:
    """Two hot namespaces carry ``hot_share`` of the load; the rest is uniform."""
    if rng.random() < hot_share:
        return namespace(rng.randint(0, 1))
    return namespace(rng.randint(2, namespaces - 1))


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summary(values: list[float], scale: float = 1000.0) -> dict[str, float]:
    return {
        "n": len(values),
        "p50": round(pct(values, 0.50) * scale, 2),
        "p95": round(pct(values, 0.95) * scale, 2),
        "p99": round(pct(values, 0.99) * scale, 2),
        "max": round(max(values) * scale, 2) if values else float("nan"),
    }


def engine_for(url: str, pool: int = 20):
    engine = create_async_engine(url, pool_size=pool, max_overflow=0)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def reset(url: str) -> None:
    engine, _ = engine_for(url, 2)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DROP TABLE IF EXISTS work_items, work_queues, work_rate_buckets, "
                "bench_effects"
            )
        )
        await conn.run_sync(
            WorkItem.metadata.create_all,
            tables=[WorkItem.__table__, WorkQueue.__table__, WorkRateBucket.__table__],
        )
        await conn.execute(
            text(
                "CREATE TABLE bench_effects (ns text, latency float8, at timestamptz DEFAULT clock_timestamp())"
            )
        )
    await engine.dispose()


# --- phase 1: enqueue ---------------------------------------------------------


async def enqueue_phase(
    url: str, total: int, writers: int, per_txn: int, namespaces: int
) -> float:
    engine, sessions = engine_for(url, writers + 2)
    rng = random.Random(1)
    per_writer = total // writers

    async def writer() -> None:
        for _ in range(per_writer // per_txn):
            async with sessions() as session:
                work = WorkWriter(session, registry=REGISTRY)
                for _ in range(per_txn):
                    await work.add(
                        "bench.noop", pick_namespace(rng, namespaces, 0.8), delay=None
                    )
                await session.commit()

    started = time.perf_counter()
    await asyncio.gather(*(writer() for _ in range(writers)))
    elapsed = time.perf_counter() - started
    await engine.dispose()
    return (per_writer // per_txn) * per_txn * writers / elapsed


# --- dispatcher processes -----------------------------------------------------


class TimedDispatcher(Dispatcher):
    """The production dispatcher, timing each claim transaction."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.claim_seconds: list[float] = []
        self.claimed = 0

    async def claim_once(self) -> list[ClaimedItem]:
        started = time.perf_counter()
        items = await super().claim_once()
        if items:
            self.claim_seconds.append(time.perf_counter() - started)
            self.claimed += len(items)
        return items


async def _live(sessions: async_sessionmaker) -> int:
    async with sessions() as session:
        return int(await session.scalar(text("SELECT count(*) FROM work_items")) or 0)


async def _worker(
    url: str, slots: int, seconds: float, owner: str, until_empty: bool
) -> dict[str, object]:
    # As a worker runs it: handler connections bounded per class
    # (WORK_DB_SLOTS_JSON), and a pool sized to exactly that budget.
    budget = DbBudget.from_settings([WorkClass.EVENTS])
    engine, sessions = engine_for(url, budget.total)
    env = ExecEnv(
        db=WorkDb(sessions, budget),
        registry=REGISTRY,
        owner=owner,
        statement_timeout_ms=2000,
    )
    dispatcher = TimedDispatcher(
        WorkClass.EVENTS, env, slots=slots, quantum=2, poll_interval=0.05
    )
    loop = asyncio.create_task(dispatcher.run())
    listener = asyncio.create_task(
        WorkWakeListener(url, {WorkClass.EVENTS: dispatcher.wake}).run()
    )
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        if until_empty and await _live(sessions) == 0:
            break
    env.stopping.set()
    dispatcher.wake()
    await loop
    listener.cancel()
    await asyncio.wait({listener})
    await dispatcher.drain(5)
    await engine.dispose()
    return {"executed": dispatcher.claimed, "claims": dispatcher.claim_seconds}


def worker_main(
    url: str, slots: int, seconds: float, owner: str, until_empty: bool, out: mp.Queue
) -> None:
    out.put(json.dumps(asyncio.run(_worker(url, slots, seconds, owner, until_empty))))


def start_workers(
    url: str, processes: int, slots: int, seconds: float, *, until_empty: bool
) -> tuple[list[mp.Process], mp.Queue]:
    ctx = mp.get_context("spawn")
    out: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=worker_main,
            args=(url, slots, seconds, f"bench{i}", until_empty, out),
        )
        for i in range(processes)
    ]
    for proc in procs:
        proc.start()
    return procs, out


def collect(procs: list[mp.Process], out: mp.Queue) -> list[dict[str, object]]:
    results = [json.loads(out.get()) for _ in procs]
    for proc in procs:
        proc.join()
    return results


async def effects_rate(url: str) -> dict[str, float]:
    """Throughput by the database's own clock, so process start-up is not counted."""
    engine, sessions = engine_for(url, 2)
    async with sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT count(*) AS n, EXTRACT(EPOCH FROM max(at) - min(at)) AS span FROM bench_effects"
                )
            )
        ).one()
    await engine.dispose()
    return {
        "executed": row.n,
        "seconds": round(float(row.span or 0), 2),
        "items_per_s": round(row.n / float(row.span)) if row.span else 0,
    }


# --- phases 2-4 ---------------------------------------------------------------


async def load_backlog(url: str, total: int, namespaces: int) -> None:
    """Skewed backlog in bulk: 80 % on two namespaces, the rest spread over the others."""
    engine, sessions = engine_for(url, 2)
    hot = int(total * 0.8)
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO work_items (id, ns, class, kind, payload, run_at) "
                "SELECT gen_random_uuid(), :a || CASE WHEN g % 2 = 0 THEN '0' ELSE '1' END "
                "  || '-0000-4000-8000-000000000000', 'events', 'bench.noop', '{}', now() "
                "FROM generate_series(1, :n) g"
            ),
            {"a": "pod:0000000", "n": hot},
        )
        await session.execute(
            text(
                "INSERT INTO work_items (id, ns, class, kind, payload, run_at) "
                "SELECT gen_random_uuid(), 'pod:' || lpad(to_hex(2 + g % (:ns - 2)), 8, '0') "
                "  || '-0000-4000-8000-000000000000', 'events', 'bench.noop', '{}', now() "
                "FROM generate_series(1, :n) g"
            ),
            {"ns": namespaces, "n": total - hot},
        )
        await session.execute(
            text(
                "INSERT INTO work_queues (class, ns, max_running) "
                "SELECT DISTINCT class, ns, 16 FROM work_items ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
        await session.execute(text("ANALYZE work_items"))
        await session.execute(text("ANALYZE work_queues"))
    await engine.dispose()


async def explain(url: str) -> dict[str, str]:
    engine, sessions = engine_for(url, 2)
    plans: dict[str, str] = {}
    statements = {
        "ready_queues": claim_sql._READY_QUEUES.text.replace(":cls", "'events'")
        .replace("CAST(:kinds AS text[])", "ARRAY['bench.noop']")
        .replace(":limit", "32"),
        "candidates": claim_sql._CANDIDATES.text.replace(":cls", "'events'")
        .replace(":ns", f"'{namespace(0)}'")
        .replace("CAST(:kinds AS text[])", "ARRAY['bench.noop']")
        .replace(":limit", "34"),
    }
    async with sessions() as session:
        for name, sql in statements.items():
            rows = await session.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql))
            plans[name] = "\n".join(r[0] for r in rows)
            await session.rollback()
    await engine.dispose()
    return plans


async def steady_producer(
    url: str, rate: float, seconds: float, namespaces: int
) -> int:
    engine, sessions = engine_for(url, 16)
    rng = random.Random(2)
    produced = 0
    started = time.monotonic()

    async def one() -> None:
        async with sessions() as session:
            await WorkWriter(session, registry=REGISTRY).add(
                "bench.noop", pick_namespace(rng, namespaces, 0.8)
            )
            await session.commit()

    pending: set[asyncio.Task[None]] = set()
    while time.monotonic() - started < seconds:
        target = int((time.monotonic() - started) * rate)
        while produced < target and len(pending) < 14:
            task = asyncio.create_task(one())
            pending.add(task)
            task.add_done_callback(pending.discard)
            produced += 1
        await asyncio.sleep(0.005)
    if pending:
        await asyncio.wait(pending)
    await engine.dispose()
    return produced


async def latencies(url: str) -> dict[str, dict[str, float]]:
    engine, sessions = engine_for(url, 2)
    async with sessions() as session:
        rows = (
            await session.execute(text("SELECT ns, latency FROM bench_effects"))
        ).all()
    await engine.dispose()
    hot = {namespace(0), namespace(1)}
    return {
        "hot": summary([r.latency for r in rows if r.ns in hot]),
        "quiet": summary([r.latency for r in rows if r.ns not in hot]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--namespaces", type=int, default=1000)
    parser.add_argument("--backlog", type=int, default=50_000)
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--slots", type=int, default=32)
    parser.add_argument("--rate", type=float, default=1000.0)
    parser.add_argument("--steady-seconds", type=float, default=20.0)
    args = parser.parse_args()
    url = args.database_url
    report: dict[str, object] = {"args": vars(args) | {"database_url": "<redacted>"}}

    asyncio.run(reset(url))
    report["enqueue_items_per_s_1_per_txn"] = round(
        asyncio.run(enqueue_phase(url, 8000, 8, 1, args.namespaces))
    )
    asyncio.run(reset(url))
    report["enqueue_items_per_s_10_per_txn"] = round(
        asyncio.run(enqueue_phase(url, 20000, 8, 10, args.namespaces))
    )

    asyncio.run(reset(url))
    asyncio.run(load_backlog(url, args.backlog, args.namespaces))
    report["explain"] = asyncio.run(explain(url))
    procs, out = start_workers(url, args.processes, args.slots, 300, until_empty=True)
    results = collect(procs, out)
    report["drain"] = {
        "backlog": args.backlog,
        **asyncio.run(effects_rate(url)),
        "claim_txn_ms": summary([x for r in results for x in r["claims"]]),  # type: ignore[union-attr]
        "items_per_claim": round(
            sum(r["executed"] for r in results)  # type: ignore[misc]
            / max(1, sum(len(r["claims"]) for r in results)),  # type: ignore[arg-type]
            1,
        ),
    }

    asyncio.run(reset(url))
    procs, out = start_workers(
        url, args.processes, args.slots, args.steady_seconds + 3, until_empty=False
    )
    time.sleep(2)  # let the dispatchers come up before load starts
    produced = asyncio.run(
        steady_producer(url, args.rate, args.steady_seconds, args.namespaces)
    )
    steady = collect(procs, out)
    report["steady"] = {
        "rate_target_per_s": args.rate,
        "produced": produced,
        "executed": sum(r["executed"] for r in steady),  # type: ignore[misc]
        "due_to_start_ms": asyncio.run(latencies(url)),
        "claim_txn_ms": summary([x for r in steady for x in r["claims"]]),  # type: ignore[union-attr]
    }
    print(json.dumps(report, indent=2))  # noqa: T201 - the report is the program's output


if __name__ == "__main__":
    main()
