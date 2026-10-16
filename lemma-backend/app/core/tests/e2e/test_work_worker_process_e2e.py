"""The work runtime inside a real ``python -m``-started worker process.

One worker, three things a deployment depends on:

* a periodic kind fires on its schedule, a poison item goes dead, and a lease
  left by a dead process is reaped and run;
* every work metric reaches an OTLP collector with its class label intact --
  the export view drops any label it does not know, so a metric can be
  emitted and still arrive as one undifferentiated series;
* SIGTERM drains at once: a running job is released, uncharged, and the
  process exits 0 well inside its grace.
"""

from __future__ import annotations

import asyncio
import signal
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import web
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.worker_process import production_worker_process

pytestmark = [pytest.mark.e2e, pytest.mark.worker, pytest.mark.drives_work]

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager

POD = "pod:cccccccc-0000-0000-0000-000000000003"
_WORK_METRICS = {
    "lemma.work.ready_oldest_age",
    "lemma.work.items",
    "lemma.work.namespaces",
    "lemma.work.claim_latency",
    "lemma.work.handler.duration",
    "lemma.work.dead",
    "lemma.work.lease_reaped",
}


@dataclass
class Collector:
    url: str
    #: metric name -> every attribute key seen on its points
    seen: dict[str, set[str]] = field(default_factory=dict)

    def record(self, body: bytes) -> None:
        request = ExportMetricsServiceRequest.FromString(body)
        for resource in request.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    kind = metric.WhichOneof("data")
                    points = getattr(metric, kind).data_points if kind else []
                    keys = self.seen.setdefault(metric.name, set())
                    for point in points:
                        keys.update(a.key for a in point.attributes)


@pytest_asyncio.fixture
async def collector() -> AsyncIterator[Collector]:
    holder: list[Collector] = []

    async def receive(request: web.Request) -> web.Response:
        holder[0].record(await request.read())
        return web.Response(status=200)

    app = web.Application()
    app.router.add_post("/v1/metrics", receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    holder.append(Collector(f"http://127.0.0.1:{port}/v1/metrics"))
    try:
        yield holder[0]
    finally:
        await runner.cleanup()


_SEED = [
    (
        "CREATE TABLE IF NOT EXISTS work_e2e_effects ("
        "id bigserial PRIMARY KEY, tag text NOT NULL, item_id uuid)"
    ),
    "TRUNCATE work_items, work_queues, work_rate_buckets, work_e2e_effects",
    (
        "INSERT INTO work_queues (class, ns, max_running) VALUES "
        "('automation', :pod, 32), ('events', :pod, 16)"
    ),
    # A job that runs until shutdown.
    (
        "INSERT INTO work_items (id, ns, class, kind) "
        "VALUES (:job, :pod, 'automation', 'e2e.work.long_job')"
    ),
    (
        "INSERT INTO work_items (id, ns, class, kind) "
        "VALUES (:poison, :pod, 'events', 'e2e.work.poison')"
    ),
    # Left running by a process that no longer exists.
    (
        "INSERT INTO work_items (id, ns, class, kind, state, lease_owner, lease_until) "
        "VALUES (:orphan, :pod, 'events', 'e2e.work.tick', 'running', 'gone:1', "
        "now() - interval '1 minute')"
    ),
]


async def test_a_real_worker_runs_schedules_exports_metrics_and_drains_on_sigterm(
    e2e_settings, db_manager, collector: Collector
) -> None:
    engine = create_async_engine(e2e_settings.database_url)
    job, poison, orphan = uuid4(), uuid4(), uuid4()

    async def scalar(sql: str, **params: object) -> object:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql), params)

    try:
        async with engine.begin() as connection:
            for statement in _SEED:
                await connection.execute(
                    text(statement),
                    {"pod": POD, "job": job, "poison": poison, "orphan": orphan},
                )

        async with production_worker_process(
            e2e_settings,
            log_prefix="lemma_work_worker",
            worker_entrypoint="app.core.tests.e2e.work_worker_entrypoint",
            extra_env={
                "WORK_CLASSES": "events,automation",
                "WORK_GAUGE_INTERVAL_SECONDS": "0.5",
                "WORK_POLL_INTERVAL_SECONDS": "0.2",
                "WORK_SHUTDOWN_GRACE_SECONDS": "10",
                "OBSERVABILITY_ENABLED": "true",
                "OTEL_TRACES_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
                "OTEL_METRICS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": collector.url,
                "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/protobuf",
                "OBSERVABILITY_METRICS_EXPORT_INTERVAL_MILLIS": "1000",
            },
        ) as worker:
            # The schedule: a periodic armed at boot fires again and again.
            await eventually(
                label="the periodic tick fired at least twice",
                probe=lambda: scalar(
                    "SELECT count(*) FROM work_e2e_effects WHERE tag = 'tick'"
                ),
                done=lambda n: int(n) >= 3,  # type: ignore[call-overload]
                timeout_seconds=20,
                fail_fast=lambda _: (
                    worker.read_log_tail() if worker.process.poll() else None
                ),
            )
            assert (
                await scalar(
                    "SELECT error_class FROM work_items WHERE id = :id", id=poison
                )
                == "permanent"
            )
            # Reaped: one lease loss charged, back in the queue behind its backoff.
            assert (
                await scalar(
                    "SELECT state || ':' || lease_losses FROM work_items WHERE id = :id",
                    id=orphan,
                )
                == "queued:1"
            )
            await eventually(
                label="the job started",
                probe=lambda: scalar(
                    "SELECT count(*) FROM work_e2e_effects WHERE tag = 'job.started'"
                ),
                done=lambda n: n == 1,
                timeout_seconds=10,
            )

            await eventually(
                label="every work metric exported with its class label",
                probe=lambda: _async(dict(collector.seen)),
                done=lambda seen: (
                    all("work_class" in seen.get(name, set()) for name in _WORK_METRICS)
                    # Per database, not per class: the change-log horizon.
                    and "database"
                    in seen.get("lemma.work.oldest_transaction_age", set())
                ),
                timeout_seconds=15,
            )
            assert "work_kind" in collector.seen["lemma.work.handler.duration"]
            assert "error_class" in collector.seen["lemma.work.dead"]

            worker.process.send_signal(signal.SIGTERM)
            signalled = time.monotonic()
            released = await eventually(
                label="the running job was released",
                probe=lambda: scalar(
                    "SELECT state || ':' || coalesce(lease_owner, '-') || ':' "
                    "|| attempts || ':' || lease_losses FROM work_items WHERE id = :id",
                    id=job,
                ),
                done=lambda row: row == "queued:-:0:0",
                timeout_seconds=10,
                interval_seconds=0.05,
            )
            drained_after = time.monotonic() - signalled
            exit_code = await asyncio.to_thread(worker.process.wait, 20)
            exited_after = time.monotonic() - signalled
            log = worker.read_log_tail()
    finally:
        await engine.dispose()

    print(
        f"released {released} {drained_after:.2f}s after SIGTERM; "
        f"exited {exit_code} after {exited_after:.2f}s"
    )
    assert drained_after < 3.0, log
    assert exit_code == 0, log
    assert exited_after < 10.0, log
    assert '"event": "work.runtime.stopping"' in log
    assert '"event": "service.stopped"' in log


async def _async(value: dict[str, set[str]]) -> dict[str, set[str]]:
    return value
