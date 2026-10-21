"""The pod-schema DDL runner, on a database of its own with a thousand schemas.

What it has to be: idempotent (a second run is a version read), safe for two
runners on one schema, resumable, unable to resurrect a dropped schema, fast
enough to backfill every pod as background work -- and the search read path
has to keep working on a schema at either version while the backfill runs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.models import WorkItem, WorkQueue
from app.core.infrastructure.work.testing import run_until_idle
from app.core.test_utils import get_postgres_url
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.file_entities import SearchMethod
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.events.pod_schema_work import (
    POD_SCHEMA_BACKFILL,
    register_pod_schema_work,
)
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHUNKS_VERSION,
    POD_SCHEMA_LATEST,
    all_pod_schemas_at_least,
    ensure_change_log,
    forget_all,
    migrate_pod_schema,
    pod_schema_version,
    schema_name_for_pod,
    schemas_below,
)
from app.modules.datastore.infrastructure.session import close_datastore_engine
from app.modules.datastore.services.search.postgres_search_service import (
    PostgresSearchService,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.embeddings import DeterministicTestEmbedder

pytestmark = pytest.mark.e2e

postgres_only_container = e2e_fixtures.postgres_only_container
postgres_only_database_url = e2e_fixtures.postgres_only_database_url

_SCHEMAS = 1_000


@pytest.fixture
async def ddl_db(
    postgres_only_container, postgres_only_database_url
) -> AsyncIterator[str]:
    """A database of its own: a thousand pod schemas must not leak into others."""
    name = f"lemma_ddl_{uuid4().hex[:12]}"
    admin = create_async_engine(
        postgres_only_database_url, isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = get_postgres_url(postgres_only_container, name)
    previous = datastore_settings.datastore_database_url
    datastore_settings.datastore_database_url = url
    await close_datastore_engine()
    forget_all()
    try:
        yield url
    finally:
        forget_all()
        await close_datastore_engine()
        datastore_settings.datastore_database_url = previous
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture
async def engine(ddl_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(ddl_db, pool_size=20, max_overflow=0)
    yield engine
    await engine.dispose()


async def _create_schemas(engine: AsyncEngine, count: int) -> list[str]:
    names = [schema_name_for_pod(uuid4()) for _ in range(count)]
    async with engine.begin() as conn:
        for name in names:
            await conn.execute(text(f'CREATE SCHEMA "{name}"'))
    return names


async def _migrate_all(engine: AsyncEngine, names: list[str], *, width: int = 16):
    queue = list(names)

    async def worker() -> None:
        while queue:
            await migrate_pod_schema(engine, queue.pop(), create=False)

    await asyncio.gather(*(worker() for _ in range(width)))


# Scale, not a contract a pull request can break on its own; the protected
# lane runs it. The small-scale tests below keep the migration's behaviour in
# front of every merge.
@pytest.mark.slow
async def test_a_thousand_schemas_migrate_once_and_a_rerun_is_a_read(
    engine: AsyncEngine,
) -> None:
    names = await _create_schemas(engine, _SCHEMAS)
    assert await schemas_below(engine, POD_SCHEMA_LATEST) == _SCHEMAS
    assert not await all_pod_schemas_at_least(engine, POD_SCHEMA_LATEST)

    started = time.perf_counter()
    await _migrate_all(engine, names)
    first = time.perf_counter() - started
    assert await all_pod_schemas_at_least(engine, POD_SCHEMA_LATEST)

    # A new process knows nothing: the rerun reads each version and stops.
    forget_all()
    started = time.perf_counter()
    await _migrate_all(engine, names)
    rerun = time.perf_counter() - started
    print(
        f"\nDDL runner: {_SCHEMAS} schemas first run {first:.1f}s, rerun {rerun:.2f}s"
    )
    assert rerun < first
    async with engine.connect() as conn:
        tables = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_tables WHERE tablename = 'search_files' "
                "AND schemaname LIKE 'pod\\_%'"
            )
        )
    assert tables == _SCHEMAS


async def test_concurrent_runners_on_the_same_schemas_both_succeed(
    engine: AsyncEngine,
) -> None:
    names = await _create_schemas(engine, 100)

    async def runner() -> list[int]:
        return [await migrate_pod_schema(engine, name) for name in names]

    first, second = await asyncio.gather(runner(), runner())

    assert first == second == [POD_SCHEMA_LATEST] * len(names)
    async with engine.connect() as conn:
        rows = await conn.scalar(
            text("SELECT count(*) FROM public.lemma_pod_schema_versions")
        )
    assert rows == len(names)


async def test_the_backfill_never_resurrects_a_dropped_schema(
    engine: AsyncEngine,
) -> None:
    gone = schema_name_for_pod(uuid4())
    assert await migrate_pod_schema(engine, gone, create=False) == 0
    async with engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :n"), {"n": gone}
        )
    assert exists == 0


@pytest.mark.slow
@pytest.mark.drives_work
async def test_the_backfill_job_migrates_every_schema_page_by_page(
    engine: AsyncEngine, postgres_only_database_url: str, monkeypatch
) -> None:
    """The periodic enqueues one admin item per schema behind, in its pod's
    namespace, a page at a time -- never a boot hook."""
    monkeypatch.setattr(
        "app.modules.datastore.events.pod_schema_work.BACKFILL_PAGE", 40
    )
    names = await _create_schemas(engine, 100)
    work_engine = create_async_engine(postgres_only_database_url)
    async with work_engine.begin() as conn:
        await conn.run_sync(
            WorkItem.metadata.create_all,
            tables=[WorkItem.__table__, WorkQueue.__table__],
            checkfirst=True,
        )
        await conn.execute(text("TRUNCATE work_items, work_queues"))
    sessions = async_sessionmaker(work_engine, expire_on_commit=False)
    registry = WorkRegistry()
    register_pod_schema_work(registry)
    from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkWriter

    async with sessions() as session:
        await WorkWriter(session, registry=registry).add(
            POD_SCHEMA_BACKFILL, SYSTEM_NAMESPACE
        )
        await session.commit()
    try:
        executed = await run_until_idle(
            sessions, registry=registry, classes=[WorkClass.ADMIN]
        )
    finally:
        async with work_engine.begin() as conn:
            await conn.execute(text("TRUNCATE work_items, work_queues"))
        await work_engine.dispose()

    assert await all_pod_schemas_at_least(engine, POD_SCHEMA_LATEST)
    # 100 migrations plus the three pages that listed them.
    assert executed == len(names) + 3


async def _legacy_chunks(engine: AsyncEngine, schema: str, file_id: UUID) -> None:
    dim = settings.embedding_dimension
    vector = "[" + ",".join(["0.1"] * dim) + "]"
    async with engine.begin() as conn:
        for index in (0, 0, 1):  # a duplicate, as concurrent indexers could leave
            await conn.execute(
                text(
                    f'INSERT INTO "{schema}".reserved_chunks '
                    "(chunk_index, file_id, content, embedding, chunk_metadata) "
                    "VALUES (:i, :f, :c, CAST(:v AS vector), CAST(:m AS jsonb))"
                ),
                {
                    "i": index,
                    "f": file_id,
                    "c": f"quarterly revenue forecast part {index}",
                    "v": vector,
                    "m": '{"path": "/finance/forecast.pdf", "parent_path": "/finance"}',
                },
            )


async def _search(service: PostgresSearchService, scope: str) -> list[str]:
    hits = await service.search(
        "quarterly revenue",
        method=SearchMethod.TEXT,
        scope_path=scope,
        file_scope=SearchFileScope.post_filtered(),
    )
    return sorted({hit.path for hit in hits})


async def test_search_reads_either_version_and_v2_keeps_the_data(
    engine: AsyncEngine,
) -> None:
    pod_id = uuid4()
    schema = schema_name_for_pod(pod_id)
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await migrate_pod_schema(engine, schema, target=CHUNKS_VERSION)
    file_id = uuid4()
    await _legacy_chunks(engine, schema, file_id)
    service = PostgresSearchService(
        pod_id, embedder=DeterministicTestEmbedder(settings.embedding_dimension)
    )

    assert await pod_schema_version(engine, schema) == CHUNKS_VERSION
    assert await _search(service, "/finance") == ["/finance/forecast.pdf"]

    await migrate_pod_schema(engine, schema)
    assert await pod_schema_version(engine, schema) == POD_SCHEMA_LATEST
    assert await _search(service, "/finance") == ["/finance/forecast.pdf"]
    assert await _search(service, "/elsewhere") == []
    async with engine.connect() as conn:
        chunks = await conn.scalar(
            text(f'SELECT count(*) FROM "{schema}".reserved_chunks')
        )
        indexed = (
            await conn.execute(
                text(
                    f'SELECT path, parent_path, chunk_count FROM "{schema}".search_files'
                )
            )
        ).one()
    assert chunks == 2
    assert tuple(indexed) == ("/finance/forecast.pdf", "/finance", 2)

    # A rename is one row at version 2, and search follows it.
    assert await service.rename_folder("/finance", "/archive/finance")
    assert await _search(service, "/archive") == ["/archive/finance/forecast.pdf"]
    assert await _search(service, "/finance") == []


async def _exists(engine: AsyncEngine, relation: str) -> bool:
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(
                text("SELECT to_regclass(:r) IS NOT NULL"), {"r": relation}
            )
        )


async def test_a_record_write_never_runs_the_chunk_rewrite(engine: AsyncEngine) -> None:
    """A schema the backfill has not reached, holding search chunks, gets the
    change log alone on its first record write; the version-2 rewrite of its
    chunks stays the backfill's, which later finds the log already there."""
    schema = schema_name_for_pod(uuid4())
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await migrate_pod_schema(engine, schema, target=CHUNKS_VERSION)
    await _legacy_chunks(engine, schema, uuid4())

    await ensure_change_log(engine, schema)

    assert await _exists(engine, f'"{schema}".reserved_changes')
    assert await _exists(engine, f'"{schema}".reserved_change_subscribers')
    assert not await _exists(engine, f'"{schema}".search_files'), "no v2 step"
    assert await pod_schema_version(engine, schema) == CHUNKS_VERSION
    await ensure_change_log(engine, schema)  # the memo: nothing to do

    await migrate_pod_schema(engine, schema)
    assert await pod_schema_version(engine, schema) == POD_SCHEMA_LATEST
    assert await _exists(engine, f'"{schema}".search_files')


async def test_a_schema_without_chunks_is_migrated_properly_on_its_first_write(
    engine: AsyncEngine,
) -> None:
    [schema] = await _create_schemas(engine, 1)

    await ensure_change_log(engine, schema)

    assert await pod_schema_version(engine, schema) == POD_SCHEMA_LATEST
    assert await _exists(engine, f'"{schema}".reserved_changes')


async def test_rerunning_the_change_log_step_takes_no_lock_a_writer_holds(
    engine: AsyncEngine,
) -> None:
    """A writer holds the change log's tables the way a record write does; the
    backfill re-running version 3 on that live schema must not wait on it.

    It used to: ``CREATE INDEX IF NOT EXISTS`` and ``ADD COLUMN IF NOT EXISTS``
    lock before they find nothing to do, and against a writer's lock order the
    two deadlocked (seen in the agent e2e suite).
    """
    from uuid import uuid4 as new_id

    from app.modules.datastore.changes.schema import (
        changes_table,
        create_change_log,
        subscribers_table,
    )

    [schema] = await _create_schemas(engine, 1)
    await ensure_change_log(engine, schema)
    async with engine.connect() as writer:
        await writer.begin()
        await writer.execute(text(f"SELECT count(*) FROM {subscribers_table(schema)}"))
        await writer.execute(
            text(
                f"INSERT INTO {changes_table(schema)} (table_id, op, record_id) "
                "VALUES (CAST(:t AS uuid), 'I', 'r1')"
            ),
            {"t": str(new_id())},
        )
        async with engine.begin() as backfill:
            await backfill.execute(text("SET LOCAL lock_timeout = '2s'"))
            await create_change_log(backfill, schema)
        await writer.rollback()
