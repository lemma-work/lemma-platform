"""Thousands of TIME schedules due in the same minute: how fast do they all fire?

Not part of any test lane. Creates a scratch database on the given server,
writes schedules the way the API does (row + ``schedule.fire`` item at its
cursor, in one transaction), all due at the same instant across many pods, and
fires them with real dispatchers in separate OS processes:

    uv run python load_tests/schedule_fire_load.py \\
        --server-url postgresql+asyncpg://test:test@127.0.0.1:5432/test \\
        --schedules 5000 --pods 500 --processes 4

Reports, by the database clock, how late each fire committed (run row
``created_at`` minus the due instant), and checks every schedule fired
exactly once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4


async def _create_database(server_url: str) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    name = f"schedule_load_{uuid4().hex[:10]}"
    admin = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    return server_url.rsplit("/", 1)[0] + f"/{name}"


async def _drop_database(server_url: str, url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    admin = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(
            text(f'DROP DATABASE IF EXISTS "{url.rsplit("/", 1)[1]}" WITH (FORCE)')
        )
    await admin.dispose()


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def worker(
    url: str, owner: str, slots: int, expected: int, deadline: float
) -> None:
    """One OS process run as the worker runs the events class: the production
    connection budget, a pool of exactly that size, LISTEN wake plus poll."""
    os.environ["DATABASE_URL"] = url
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.modules.schedule.work.registration  # noqa: F401
    from app.core.infrastructure.work import WorkClass, registry
    from app.core.infrastructure.work.db import DbBudget, WorkDb
    from app.core.infrastructure.work.dispatcher import Dispatcher
    from app.core.infrastructure.work.execute import ExecEnv
    from app.core.infrastructure.work.listener import WorkWakeListener
    from app.modules.test_support.e2e_base import _import_e2e_models

    _import_e2e_models()
    if os.environ.get("SCHEDULE_LOAD_NO_NOTIFY") == "1":
        # Experiment: what NOTIFY's commit-time lock costs a burst of fires.
        from app.core.infrastructure.work.writer import WorkWriter

        async def _quiet(self: WorkWriter, cls: WorkClass) -> None:
            del self, cls

        WorkWriter._notify = _quiet  # type: ignore[method-assign]
    budget = DbBudget.from_settings([WorkClass.EVENTS])
    engine = create_async_engine(url, pool_size=budget.total + 1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    env = ExecEnv(
        db=WorkDb(sessions, budget),
        registry=registry,
        owner=owner,
        statement_timeout_ms=2000,
    )
    dispatcher = Dispatcher(
        WorkClass.EVENTS, env, slots=slots, quantum=2, poll_interval=1.0
    )
    loop = asyncio.create_task(dispatcher.run())
    listener = asyncio.create_task(
        WorkWakeListener(url, {WorkClass.EVENTS: dispatcher.wake}).run()
    )
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        async with sessions() as session:
            fired = await session.scalar(text("SELECT count(*) FROM schedule_runs"))
        if (fired or 0) >= expected:
            break
    loop.cancel()
    listener.cancel()
    await asyncio.gather(loop, listener, return_exceptions=True)
    await engine.dispose()


async def run(args: argparse.Namespace) -> dict[str, object]:
    url = await _create_database(args.server_url)
    os.environ["DATABASE_URL"] = url
    try:
        return await _measure(args, url)
    finally:
        await _drop_database(args.server_url, url)


async def _measure(args: argparse.Namespace, url: str) -> dict[str, object]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.infrastructure.db.base import Base
    from app.modules.schedule.repositories.time_arming import arm_time_fire
    from app.modules.test_support.e2e_base import _import_e2e_models

    _import_e2e_models()
    engine = create_async_engine(url, pool_size=12, max_overflow=0)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Fabricated pods and owner: foreign keys are not what is measured.
        await conn.execute(
            text(
                """
                DO $$ DECLARE c record; BEGIN
                  FOR c IN SELECT conrelid::regclass AS t, conname FROM pg_constraint
                           WHERE contype = 'f'
                             AND conrelid::regclass::text IN ('schedules', 'schedule_runs')
                  LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', c.t, c.conname);
                  END LOOP; END $$
                """
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    pods = [uuid4() for _ in range(args.pods)]
    owner = uuid4()
    due = (datetime.now(timezone.utc) + timedelta(seconds=args.lead_seconds)).replace(
        microsecond=0
    )
    write_started = time.perf_counter()
    if args.warm_queues:
        # Steady state: every pod has fired before, so its queue rows exist.
        async with sessions() as session:
            for pod_id in pods:
                for cls, cap in (("events", 16), ("automation", 32)):
                    await session.execute(
                        text(
                            "INSERT INTO work_queues (class, ns, max_running) "
                            "VALUES (:cls, :ns, :cap) ON CONFLICT DO NOTHING"
                        ),
                        {"cls": cls, "ns": f"pod:{pod_id}", "cap": cap},
                    )
            await session.commit()

    async def write(chunk: range) -> None:
        async with sessions() as session:
            for n in chunk:
                schedule_id = uuid4()
                pod_id = pods[n % len(pods)]
                await session.execute(
                    text(
                        "INSERT INTO schedules (id, user_id, pod_id, schedule_type, "
                        "agent_id, config, instruction, visibility, is_active, "
                        "is_internal, next_fire_at, include_outside_rows, "
                        "consecutive_failures, created_at, updated_at) VALUES "
                        "(:id, :uid, :pod, 'TIME', :pod, '{\"cron\": \"0 * * * *\"}', "
                        "'hi', 'POD', true, false, :due, false, 0, now(), now())"
                    ),
                    {"id": schedule_id, "uid": owner, "pod": pod_id, "due": due},
                )
                await arm_time_fire(
                    session, schedule_id=schedule_id, pod_id=pod_id, fire_at=due
                )
            await session.commit()

    step = 250
    chunks = [
        range(i, min(args.schedules, i + step)) for i in range(0, args.schedules, step)
    ]
    for wave in range(0, len(chunks), 8):
        await asyncio.gather(*(write(c) for c in chunks[wave : wave + 8]))
    wrote = time.perf_counter() - write_started
    if datetime.now(timezone.utc) >= due:
        raise SystemExit("writing took longer than the lead; raise --lead-seconds")

    procs = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            __file__,
            "--worker",
            "--url",
            url,
            "--owner",
            f"w{n}",
            "--slots",
            str(args.slots),
            "--expected",
            str(args.schedules),
            "--deadline",
            str(due.timestamp() + args.give_up_seconds),
        )
        for n in range(args.processes)
    ]
    codes = await asyncio.gather(*(p.wait() for p in procs))

    async with sessions() as session:
        latencies = [
            float(row[0])
            for row in (
                await session.execute(
                    text(
                        "SELECT extract(epoch FROM created_at - :due) FROM schedule_runs"
                    ),
                    {"due": due},
                )
            ).all()
        ]
        duplicates = await session.scalar(
            text(
                "SELECT count(*) FROM (SELECT schedule_id FROM schedule_runs "
                "GROUP BY 1 HAVING count(*) > 1) d"
            )
        )
        starts = await session.scalar(
            text("SELECT count(*) FROM work_items WHERE kind = 'schedule.start_target'")
        )
        next_fires = await session.scalar(
            text(
                "SELECT count(*) FROM work_items WHERE kind = 'schedule.fire' "
                "AND state = 'queued' AND run_at > :due"
            ),
            {"due": due},
        )
    await engine.dispose()
    return {
        "schedules": args.schedules,
        "pods": args.pods,
        "processes": args.processes,
        "slots_per_process": args.slots,
        "warm_queues": args.warm_queues,
        "worker_exit_codes": codes,
        "write_seconds": round(wrote, 1),
        "fired": len(latencies),
        "duplicate_fires": duplicates,
        "start_items": starts,
        "next_fire_items": next_fires,
        "fire_latency_s": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(_pct(latencies, 0.95), 3),
            "p99": round(_pct(latencies, 0.99), 3),
            "max": round(max(latencies), 3),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url")
    parser.add_argument("--schedules", type=int, default=5000)
    parser.add_argument("--pods", type=int, default=500)
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--slots", type=int, default=32)
    parser.add_argument("--lead-seconds", type=int, default=90)
    parser.add_argument("--warm-queues", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--owner")
    parser.add_argument("--expected", type=int)
    parser.add_argument("--deadline", type=float)
    parser.add_argument("--give-up-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(
            worker(args.url, args.owner, args.slots, args.expected, args.deadline)
        )
        return
    print(json.dumps(asyncio.run(run(args)), indent=1))  # noqa: T201 - the report


if __name__ == "__main__":
    main()
