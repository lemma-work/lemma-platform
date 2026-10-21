"""Pod live socket under load: many sockets, many pods, two API processes.

Not part of any test lane. Run it explicitly against the e2e containers:

    LEMMA_E2E_REUSE=1 uv run pytest load_tests/live_socket_bench.py -s \\
        -o python_files=live_socket_bench.py

It starts two real API processes (two pod hubs per watched pod), creates the
pods and tables, then runs two phases with the same write load -- a few
sockets, then many -- so the database work the hubs do can be compared at two
socket counts. Clients run in their own processes so their parsing does not sit
on the measured path. Results are printed as JSON.

What each number means:

* ``log_reads`` -- index scans of the pods' ``reserved_changes``: one per hub
  read of the change log. Writes insert into it and do not scan it.
* ``row_fetches`` -- scans of the watched tables: one per current-row fetch.
  Single-row writes insert and do not scan.
* ``datastore_transactions`` -- every transaction on the datastore database,
  writes included.
* ``latency`` -- from the writer stamping the row to a client parsing the
  change, same clock.
* ``footprint_per_socket_kib`` -- the API processes' memory footprint growth across
  a phase's connects, divided by the sockets it added.

The client half runs as ``python load_tests/live_socket_bench.py clients ...``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

TABLES = ("t0", "t1", "t2")


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


# ---------------------------------------------------------------------------
# Clients (a subprocess)
# ---------------------------------------------------------------------------


async def _client_main(args: argparse.Namespace) -> None:
    from websockets.asyncio.client import connect

    _raise_open_file_limit()

    urls: list[str] = args.urls.split(",")
    pods: list[str] = args.pods.split(",")
    latencies: list[float] = []
    counts = {"frames": 0, "changes": 0, "resyncs": 0, "errors": 0, "closed": 0}
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    sockets = []

    async def open_one(index: int) -> None:
        pod = pods[(args.offset + index) % len(pods)]
        url = f"{urls[(args.offset + index) % len(urls)]}/pods/{pod}/live"
        ws = await connect(
            url,
            additional_headers={"Authorization": f"Bearer {args.token}"},
            max_size=None,
            open_timeout=60,
        )
        ready = json.loads(await ws.recv())
        assert ready["type"] == "ready", ready
        for n, table in enumerate(TABLES):
            await ws.send(
                json.dumps({"op": "subscribe", "id": f"s{n}", "table": table})
            )
        for _ in TABLES:
            frame = json.loads(await ws.recv())
            assert frame["type"] == "subscribed", frame
        sockets.append(ws)

    gate = asyncio.Semaphore(50)

    async def guarded(index: int) -> None:
        async with gate:
            await open_one(index)

    await asyncio.gather(*(guarded(i) for i in range(args.sockets)))
    print("READY", flush=True)  # noqa: T201 - the parent waits for this line

    async def pump(ws) -> None:
        try:
            async for raw in ws:
                now = time.time()
                frame = json.loads(raw)
                counts["frames"] += 1
                kind = frame.get("type")
                if kind == "changes":
                    for change in frame["changes"]:
                        counts["changes"] += 1
                        row = change.get("row") or {}
                        stamp = row.get("body")
                        if isinstance(stamp, str):
                            latencies.append(now - float(stamp))
                elif kind == "resync":
                    counts["resyncs"] += 1
                elif kind in ("error", "unsubscribed"):
                    counts["errors"] += 1
        except Exception:
            counts["closed"] += 1

    pumps = [asyncio.create_task(pump(ws)) for ws in sockets]
    await stop.wait()
    for task in pumps:
        task.cancel()
    Path(args.out).write_text(json.dumps({"counts": counts, "latencies": latencies}))
    for ws in sockets:
        await ws.close()


# ---------------------------------------------------------------------------
# API process (a subprocess): the real app, with its change-log calls counted
# ---------------------------------------------------------------------------


def _api_main(args: argparse.Namespace) -> None:
    """Serve the app and write how often it read the change log, every second.

    The counters wrap the reader's module functions, which the hub calls by
    attribute, so every hub read is counted and nothing else changes.
    """
    import threading

    import uvicorn

    from app.core.server_limits import WS_MAX_BYTES
    from app.modules.datastore.changes import reader

    counts = {"read_after": 0, "fetch_rows": 0, "current_horizon": 0}

    def counted(name: str):
        original = getattr(reader, name)

        async def wrapper(*a, **kw):
            counts[name] += 1
            return await original(*a, **kw)

        setattr(reader, name, wrapper)

    for name in counts:
        counted(name)

    def dump() -> None:
        while True:
            Path(args.counts).write_text(json.dumps(counts))
            time.sleep(0.5)

    threading.Thread(target=dump, daemon=True).start()
    uvicorn.run(
        "app.app:app",
        host="127.0.0.1",
        port=args.port,
        ws="websockets-sansio",
        ws_max_size=WS_MAX_BYTES,
        log_level="warning",
    )


def _hub_calls(workdir: Path) -> dict[str, int]:
    total: dict[str, int] = {}
    for path in workdir.glob("api-*.counts.json"):
        for key, value in json.loads(path.read_text()).items():
            total[key] = total.get(key, 0) + value
    return total


# ---------------------------------------------------------------------------
# Orchestration (pytest, against the e2e containers)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _rss_kib(pid: int) -> int:
    """The process's memory footprint, compressed pages included.

    ``ps`` RSS on macOS drops as the compressor takes idle pages, which makes
    it useless for a before/after difference; ``footprint`` counts them.
    Falls back to RSS where ``footprint`` does not exist.
    """
    out = subprocess.run(["footprint", str(pid)], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if "Footprint:" in line:
            value, unit = line.split("Footprint:", 1)[1].split()[:2]
            scale = {"KB": 1, "MB": 1024, "GB": 1024 * 1024}.get(unit, 1)
            return int(float(value) * scale)
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True
    )
    return int(out.stdout.strip() or 0)


def _api_env(e2e_settings) -> dict[str, str]:
    from app.core.config import settings
    from app.modules.datastore.config import datastore_settings

    return {
        **os.environ,
        "PYTHONPATH": ".",
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": e2e_settings.database_url,
        "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url,
        "REDIS_URL": e2e_settings.redis_url,
        "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
        "SUPERTOKENS_ENV": "testing",
        # Not "testing": that runs every engine on NullPool, a connection per
        # query, which measures connection setup rather than the hub.
        "ENVIRONMENT": "local",
        "DB_POOL_SIZE": "8",
        "EMAIL_TRANSPORT": "filesystem",
        "EMAIL_OUTPUT_DIR": settings.email_output_dir,
        "STORAGE_BACKEND": "local",
        "EMBEDDING_PROVIDER": "local",
        "LOCAL_OBJECT_STORAGE_ROOT": e2e_settings.local_object_storage_root,
        "LOCAL_FILE_STORAGE_ROOT": e2e_settings.local_file_storage_root,
        "DATASTORE_CHANGE_LOG_MODE": "on",
        "LIVE_MAX_SOCKETS_PER_USER_PER_POD": "100000",
        "LOG_LEVEL": "WARNING",
    }


async def _start_api(env: dict[str, str], port: int, log: Path) -> subprocess.Popen:
    import httpx

    backend = Path(__file__).resolve().parents[1]
    # Popen returns at once; the readiness wait below is async.
    process = subprocess.Popen(  # noqa: ASYNC220
        [
            str(backend / ".venv/bin/python"),
            str(Path(__file__).resolve()),
            "api",
            "--port",
            str(port),
            "--counts",
            str(log.with_suffix(".counts.json")),
        ],
        cwd=str(backend),
        env=env,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    async with httpx.AsyncClient() as client:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"API exited: {log.read_text()[-4000:]}")
            try:
                if (
                    await client.get(f"http://127.0.0.1:{port}/livez")
                ).status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("API did not start")


async def _datastore_counters(engine) -> dict[str, int]:
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        log = (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(idx_scan),0), coalesce(sum(seq_scan),0) "
                    "FROM pg_stat_user_tables WHERE relname = 'reserved_changes'"
                )
            )
        ).one()
        rows = (
            await conn.execute(
                text(
                    "SELECT coalesce(sum(coalesce(idx_scan,0) + seq_scan),0) "
                    "FROM pg_stat_user_tables WHERE relname = ANY(:t)"
                ),
                {"t": list(TABLES)},
            )
        ).scalar_one()
        xacts = (
            await conn.execute(
                text(
                    "SELECT xact_commit + xact_rollback FROM pg_stat_database "
                    "WHERE datname = current_database()"
                )
            )
        ).scalar_one()
    return {
        "log_reads": int(log[0]) + int(log[1]),
        "row_fetches": int(rows),
        "datastore_transactions": int(xacts),
    }


async def _table_contexts(table_ids: dict[tuple[str, str], str]):
    """The write path's view of each bench table, loaded once."""
    from uuid import UUID

    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.datastore.changes.capture import ChangeCapture
    from app.modules.datastore.infrastructure.pod_schema_migrations import (
        schema_name_for_pod,
    )
    from app.modules.datastore.services.table_context import TableContext
    from app.modules.datastore.services.wiring import build_table_service

    contexts = {}
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        repository = build_table_service(uow).table_repository
        for key, table_id in table_ids.items():
            entity = await repository.get(UUID(table_id))
            assert entity is not None
            contexts[key] = TableContext.from_table_entity(
                entity, schema_name_for_pod(entity.pod_id)
            )
    for ctx in contexts.values():
        await ChangeCapture(ctx).prepare()
    return contexts


async def _write_load(
    contexts, pods: list[str], rate: float, seconds: float, engine
) -> dict[str, float]:
    """Single-row inserts through the change log's own write path.

    Each write is one transaction: the row insert wrapped by ``ChangeCapture``
    (the statement the record repository runs), commit, then the Redis wake.
    It skips HTTP and session checks on purpose: those cost the API processes
    CPU that has nothing to do with the hub this measures.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.datastore.changes.capture import ChangeCapture

    sent = failed = 0
    interval = 1.0 / rate
    in_flight: set[asyncio.Task] = set()

    async def one(n: int) -> None:
        nonlocal failed
        pod = pods[n % len(pods)]
        table = TABLES[(n // len(pods)) % len(TABLES)]
        ctx = contexts[(pod, table)]
        capture = ChangeCapture(ctx)
        try:
            async with AsyncSession(engine) as session, session.begin():
                await capture.begin(session)
                sql, params = capture.wrap(
                    f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" (body) '
                    "VALUES (:body) RETURNING *",
                    {"body": f"{time.time():.6f}"},
                    op="I",
                )
                await session.execute(text(sql), params)
            await capture.publish_wake()
        except Exception:
            failed += 1

    start = time.monotonic()
    n = 0
    while time.monotonic() - start < seconds:
        due = start + n * interval
        if due > time.monotonic():
            await asyncio.sleep(due - time.monotonic())
        task = asyncio.create_task(one(n))
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)
        n += 1
        sent += 1
    await asyncio.gather(*in_flight)
    duration = time.monotonic() - start
    return {
        "sent": sent,
        "failed": failed,
        "seconds": duration,
        "rate": sent / duration,
    }


async def _run_clients(
    urls: list[str],
    token: str,
    pods: list[str],
    sockets: int,
    processes: int,
    workdir: Path,
) -> tuple[list[subprocess.Popen], list[Path]]:
    procs: list[subprocess.Popen] = []
    outs: list[Path] = []
    per = sockets // processes
    for index in range(processes):
        out = workdir / f"clients-{sockets}-{index}.json"
        outs.append(out)
        procs.append(
            subprocess.Popen(  # noqa: ASYNC220 - returns at once; READY is awaited below
                [
                    sys.executable,
                    __file__,
                    "clients",
                    "--urls",
                    ",".join(u.replace("http://", "ws://") for u in urls),
                    "--pods",
                    ",".join(pods),
                    "--token",
                    token,
                    "--sockets",
                    str(per),
                    "--offset",
                    str(index * per),
                    "--out",
                    str(out),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
        )
    for proc in procs:
        assert proc.stdout is not None
        line = await asyncio.to_thread(proc.stdout.readline)
        assert line.strip() == "READY", line
    return procs, outs


async def _stop_clients(procs: list[subprocess.Popen], outs: list[Path]) -> dict:
    for proc in procs:
        proc.send_signal(signal.SIGTERM)
    for proc in procs:
        await asyncio.to_thread(proc.wait, 60)
    counts: dict[str, int] = {}
    latencies: list[float] = []
    for out in outs:
        data = json.loads(out.read_text())
        for key, value in data["counts"].items():
            counts[key] = counts.get(key, 0) + value
        latencies.extend(data["latencies"])
    return {
        "counts": counts,
        "latency_ms": {
            "p50": round((pct(latencies, 0.50) or 0) * 1000, 1),
            "p95": round((pct(latencies, 0.95) or 0) * 1000, 1),
            "p99": round((pct(latencies, 0.99) or 0) * 1000, 1),
            "max": round(max(latencies, default=0) * 1000, 1),
            "samples": len(latencies),
        },
    }


def _raise_open_file_limit() -> None:
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = 20_000 if hard == resource.RLIM_INFINITY else min(hard, 20_000)
    if soft < wanted:
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))


if __name__ != "__main__":
    from app.modules.test_support.e2e import fixtures as e2e_fixtures

    postgres_container = e2e_fixtures.postgres_container
    supertokens_container = e2e_fixtures.supertokens_container
    redis_container = e2e_fixtures.redis_container
    test_database_url = e2e_fixtures.test_database_url
    test_redis_url = e2e_fixtures.test_redis_url
    e2e_settings = e2e_fixtures.e2e_settings
    db_manager = e2e_fixtures.db_manager
    test_app = e2e_fixtures.test_app
    db_session = e2e_fixtures.db_session
    async_client = e2e_fixtures.async_client
    e2e_process_clients = e2e_fixtures.e2e_process_clients
    fixed_test_user = e2e_fixtures.fixed_test_user
    authenticated_client = e2e_fixtures.authenticated_client
    fixed_test_org = e2e_fixtures.fixed_test_org


async def test_live_socket_load(
    e2e_settings, authenticated_client, fixed_test_org, fixed_test_user, tmp_path
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.modules.datastore.config import datastore_settings

    pods_n = int(os.environ.get("LIVE_BENCH_PODS", "20"))
    many = int(os.environ.get("LIVE_BENCH_SOCKETS", "1000"))
    few = int(os.environ.get("LIVE_BENCH_FEW_SOCKETS", "40"))
    rate = float(os.environ.get("LIVE_BENCH_WRITES_PER_SECOND", "200"))
    seconds = float(os.environ.get("LIVE_BENCH_SECONDS", "30"))
    token = fixed_test_user["token"]
    _raise_open_file_limit()  # inherited by the API and client processes

    pods: list[str] = []
    table_ids: dict[tuple[str, str], str] = {}
    for n in range(pods_n):
        created = await authenticated_client.post(
            "/pods",
            json={
                "name": f"Live bench {n}",
                "slug": f"live-bench-{os.getpid()}-{n}-{int(time.time())}",
                "type": "ASSISTANT",
                "organization_id": fixed_test_org["id"],
            },
        )
        assert created.status_code == 201, created.text
        pod_id = created.json()["id"]
        for table in TABLES:
            made = await authenticated_client.post(
                f"/pods/{pod_id}/datastore/tables",
                json={
                    "name": table,
                    "enable_rls": False,
                    "columns": [{"name": "body", "type": "TEXT", "required": False}],
                },
            )
            assert made.status_code in (200, 201), made.text
            table_ids[(pod_id, table)] = made.json()["id"]
        pods.append(pod_id)
    contexts = await _table_contexts(table_ids)

    env = _api_env(e2e_settings)
    ports = [_free_port(), _free_port()]
    workdir = Path(os.environ.get("LIVE_BENCH_DIR") or tmp_path)
    workdir.mkdir(parents=True, exist_ok=True)
    apis = [await _start_api(env, port, workdir / f"api-{port}.log") for port in ports]
    urls = [f"http://127.0.0.1:{port}" for port in ports]
    engine = create_async_engine(datastore_settings.datastore_database_url)
    writer_engine = create_async_engine(
        datastore_settings.datastore_database_url, pool_size=20, max_overflow=0
    )
    results: dict[str, object] = {
        "pods": pods_n,
        "api_processes": len(apis),
        "subscriptions_per_socket": len(TABLES),
        "target_writes_per_second": rate,
        "seconds": seconds,
    }
    try:
        await asyncio.sleep(2)
        rss_idle = [_rss_kib(api.pid) for api in apis]
        for label, sockets, client_procs in (("few", few, 1), ("many", many, 2)):
            rss_before = [_rss_kib(api.pid) for api in apis]
            procs, outs = await _run_clients(
                urls, token, pods, sockets, client_procs, workdir
            )
            await asyncio.sleep(2)
            rss_connected = [_rss_kib(api.pid) for api in apis]
            await asyncio.sleep(3)
            before = await _datastore_counters(engine)
            calls_before = _hub_calls(workdir)
            writes = await _write_load(contexts, pods, rate, seconds, writer_engine)
            calls_after = _hub_calls(workdir)
            await asyncio.sleep(3)
            after = await _datastore_counters(engine)
            received = await _stop_clients(procs, outs)
            per_second = {
                key: round((after[key] - before[key]) / writes["seconds"], 1)
                for key in after
            }
            results[label] = {
                "hub_calls_per_second": {
                    key: round(
                        (calls_after[key] - calls_before.get(key, 0))
                        / writes["seconds"],
                        1,
                    )
                    for key in calls_after
                },
                "sockets": sockets,
                "subscriptions": sockets * len(TABLES),
                "writes": writes,
                "db_per_second": per_second,
                "expected_deliveries": round(writes["sent"] * sockets / pods_n),
                **received,
                "footprint_kib_before_connect": rss_before,
                "footprint_kib_connected": rss_connected,
                "footprint_per_socket_kib": round(
                    (sum(rss_connected) - sum(rss_before)) / max(1, sockets), 1
                ),
            }
            await asyncio.sleep(12)  # let idle hubs linger out between phases
        results["rss_kib_idle"] = rss_idle
    finally:
        for api in apis:
            api.terminate()
        for api in apis:
            api.wait(30)
        await engine.dispose()
        await writer_engine.dispose()
    print("LIVE_BENCH_RESULTS", json.dumps(results, indent=2))  # noqa: T201 - the report
    out = os.environ.get("LIVE_BENCH_OUT")
    if out:
        Path(out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    clients = sub.add_parser("clients")
    clients.add_argument("--urls", required=True)
    clients.add_argument("--pods", required=True)
    clients.add_argument("--token", required=True)
    clients.add_argument("--sockets", type=int, required=True)
    clients.add_argument("--offset", type=int, default=0)
    clients.add_argument("--out", required=True)
    api = sub.add_parser("api")
    api.add_argument("--port", type=int, required=True)
    api.add_argument("--counts", required=True)
    parsed = parser.parse_args()
    if parsed.mode == "api":
        _api_main(parsed)
    else:
        asyncio.run(_client_main(parsed))
