"""File processing under a noisy neighbour: does one pod's bulk upload starve the rest?

Not part of any test lane. Creates a scratch database on the given server,
writes uploads the way the write path does (row, bytes and ``file.process``
item in one transaction), and drains them with real dispatchers through the
real pipeline -- the fake Kreuzberg from the datastore e2e suite at the HTTP
boundary, a deterministic embedder, the pod-data tables in the same database:

    uv run python load_tests/file_work_fairness.py \\
        --server-url postgresql+asyncpg://test:test@127.0.0.1:5432/test

The noisy pod's whole backlog is written first, then everybody else's -- the
order a first-in-first-out queue would serve worst. Reports time-to-indexed
(row ``indexed_at`` minus ``created_at``) per pod group.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import tempfile
import time
from uuid import UUID, uuid4


def _configure_environment(database_url: str, storage_root: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    os.environ["DATASTORE_DATABASE_URL"] = database_url
    os.environ["LOCAL_OBJECT_STORAGE_ROOT"] = storage_root
    os.environ["DOCUMENT_PROCESSING_LAYOUT_ENABLED"] = "false"
    os.environ["DOCUMENT_PROCESSING_DEBOUNCE_SECONDS"] = "0"


async def _create_database(server_url: str) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    name = f"file_fairness_{uuid4().hex[:10]}"
    admin = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    return server_url.rsplit("/", 1)[0] + f"/{name}"


async def _drop_database(server_url: str, url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    name = url.rsplit("/", 1)[1]
    admin = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await admin.dispose()


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50_s": round(statistics.median(values), 2),
        "p95_s": round(_pct(values, 0.95), 2),
        "max_s": round(max(values), 2),
    }


async def run(args: argparse.Namespace) -> dict[str, object]:
    url = await _create_database(args.server_url)
    storage_root = tempfile.mkdtemp(prefix="file-fairness-")
    _configure_environment(url, storage_root)

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.infrastructure.db.base import Base
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.core.infrastructure.work import WorkClass
    from app.core.infrastructure.work.testing import dispatcher_for
    from app.modules.datastore.composition import (
        DatastoreComposition,
        install_datastore_composition,
    )
    from app.modules.datastore.config import datastore_settings
    from app.modules.datastore.domain.file_entities import (
        DatastoreFileEntity,
        FileStatus,
    )
    from app.modules.datastore.infrastructure.repositories.file_repository import (
        DatastoreFileRepository,
    )
    from app.modules.datastore.infrastructure.storage import create_datastore_storage
    from app.modules.datastore.infrastructure.storage_paths import content_keys
    from app.modules.datastore.tests.e2e.fake_document_processors import (
        FakeDocumentProcessorServer,
    )
    from app.modules.datastore.tests.e2e.file_work_support import file_work_registry
    from app.modules.datastore.tests.e2e.harness import build_pdf_bytes
    from app.modules.test_support.e2e_base import _import_e2e_models
    from app.modules.test_support.embeddings import DeterministicTestEmbedder

    _import_e2e_models()
    embedder = DeterministicTestEmbedder(settings.embedding_dimension)
    install_datastore_composition(
        DatastoreComposition(embedder_provider=lambda: embedder)
    )
    server = FakeDocumentProcessorServer()
    await server.start()
    datastore_settings.kreuzberg_url = server.base_url
    datastore_settings.document_processor = "kreuzberg"
    datastore_settings.e2e_disable_worker_file_autoindex = False

    engine = create_async_engine(url, pool_size=40, max_overflow=0)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = create_datastore_storage()

    noisy = uuid4()
    quiet = [uuid4() for _ in range(args.pods - 1)]
    per_quiet = (args.uploads - args.noisy_uploads) // len(quiet)
    owner = uuid4()
    plan: list[UUID] = [noisy] * args.noisy_uploads
    plan += [pod for _ in range(per_quiet) for pod in quiet]

    async def upload(pod_id: UUID, index: int) -> None:
        name = f"{args.prefix}doc-{index}.pdf"
        content = build_pdf_bytes(f"{pod_id} {index}")
        entity = DatastoreFileEntity(
            pod_id=pod_id,
            owner_user_id=owner,
            path=f"/{name}",
            name=name,
            mime_type="application/pdf",
            size_bytes=len(content),
            visibility="POD",
            status=FileStatus.NOT_REQUIRED,
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
        await storage.upload_file(content_keys(entity).original, content)
        async with sessions() as session:
            # Fabricated pods and owner: foreign keys are not what is measured.
            await session.execute(
                text("SET LOCAL session_replication_role = 'replica'")
            )
            uow = SqlAlchemyUnitOfWork(session)
            entity.mark_created(owner)
            await DatastoreFileRepository(uow).create(entity)
            await uow.commit()

    started = time.perf_counter()
    for index, pod_id in enumerate(plan):
        await upload(pod_id, index)
    wrote = time.perf_counter() - started

    registry = file_work_registry()
    dispatchers = [
        dispatcher_for(
            sessions,
            WorkClass.INGESTION,
            registry=registry,
            owner=f"worker-{n}",
            slots=args.slots,
        )
        for n in range(args.workers)
    ]

    async def loop(dispatcher) -> None:
        running: set[asyncio.Task[None]] = set()
        while True:
            await dispatcher.reap()
            free = args.slots - len(running)
            items = await dispatcher.claim_once() if free > 0 else []
            for item in items:
                task = asyncio.create_task(dispatcher.execute(item))
                running.add(task)
                task.add_done_callback(running.discard)
            async with sessions() as session:
                left = await session.scalar(
                    text("SELECT count(*) FROM work_items WHERE state <> 'dead'")
                )
            if not left and not running:
                return
            await asyncio.sleep(0.02 if items else 0.1)

    drain_started = time.perf_counter()
    await asyncio.gather(*(loop(d) for d in dispatchers))
    drained = time.perf_counter() - drain_started

    async with sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT pod_id, status, "
                    "EXTRACT(EPOCH FROM indexed_at - created_at) AS waited "
                    "FROM datastore_files"
                )
            )
        ).all()
    await server.stop()
    await engine.dispose()
    await _drop_database(args.server_url, url)

    noisy_waits = [float(r.waited) for r in rows if r.pod_id == noisy and r.waited]
    quiet_waits = [float(r.waited) for r in rows if r.pod_id != noisy and r.waited]
    per_pod_p95 = {}
    for pod in quiet:
        waits = [float(r.waited) for r in rows if r.pod_id == pod and r.waited]
        per_pod_p95[str(pod)] = _pct(waits, 0.95)
    return {
        "uploads": len(plan),
        "pods": args.pods,
        "noisy_uploads": args.noisy_uploads,
        "workers": args.workers,
        "slots_per_worker": args.slots,
        "completed": sum(1 for r in rows if r.status == "COMPLETED"),
        "write_seconds": round(wrote, 1),
        "drain_seconds": round(drained, 1),
        "throughput_per_s": round(len(plan) / drained, 1),
        "time_to_indexed_noisy_pod": _stats(noisy_waits),
        "time_to_indexed_other_pods": _stats(quiet_waits),
        "worst_other_pod_p95_s": round(max(per_pod_p95.values()), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--uploads", type=int, default=2_000)
    parser.add_argument("--pods", type=int, default=50)
    parser.add_argument("--noisy-uploads", type=int, default=1_500)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument(
        "--prefix",
        default="delayed-",
        help="``delayed-`` makes each extraction take 0.4s at the fake server",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), indent=2))  # noqa: T201 - the report


if __name__ == "__main__":
    main()
