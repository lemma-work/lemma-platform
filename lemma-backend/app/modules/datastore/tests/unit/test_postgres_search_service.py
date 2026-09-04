from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.modules.datastore.domain.file_entities import DatastoreFileSearchResult
from app.modules.datastore.infrastructure.reranker import NoopReranker
from app.modules.datastore.infrastructure.schema_manager import (
    SchemaManager,
)
from app.modules.datastore.services.search.postgres_search_service import (
    PostgresSearchService,
)


class _FakeEmbedder:
    async def embed(self, text: str):
        return [0.0]

    async def embed_batch(self, texts: list[str]):
        return [[0.0] for _ in texts]


def _search_service() -> PostgresSearchService:
    return PostgresSearchService(
        uuid4(),
        engine=object(),
        session_factory=object(),
        embedder=_FakeEmbedder(),
        reranker=NoopReranker(),
    )


def _result(file_id, chunk_index: int, score: float) -> dict:
    return {
        "file_id": file_id,
        "chunk_index": chunk_index,
        "path": f"/files/{file_id}.md",
        "content": f"chunk {chunk_index}",
        "metadata": {},
        "score": score,
    }


def _obj(file_id, chunk_index: int, score: float = 1.0) -> DatastoreFileSearchResult:
    return DatastoreFileSearchResult(
        file_id=file_id,
        path=f"/files/{file_id}.md",
        chunk_index=chunk_index,
        content=f"chunk {chunk_index}",
        metadata={},
        score=score,
    )


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Transaction(self.connection)


@pytest.mark.asyncio
async def test_ensure_schema_does_not_recreate_installed_vector_extension():
    connection = AsyncMock()
    connection.scalar.return_value = True
    service = PostgresSearchService(
        uuid4(),
        engine=_Engine(connection),
        session_factory=object(),
        embedder=_FakeEmbedder(),
        reranker=NoopReranker(),
    )

    await service.ensure_schema()

    statements = [str(call.args[0]) for call in connection.execute.await_args_list]
    assert not any(statement.startswith("CREATE EXTENSION") for statement in statements)
    connection.scalar.assert_awaited_once()


def test_merge_ranked_results_combines_text_and_vector_ranks():
    service = _search_service()
    file_id = uuid4()

    merged = service._merge_ranked_results(
        [_result(file_id, 0, 0.70)],
        [_result(file_id, 0, 0.50)],
    )

    assert len(merged) == 1
    assert merged[0]["file_id"] == file_id
    # Present in both result sets, so the RRF score sums both contributions.
    assert merged[0]["score"] > 1 / 61


def test_merge_ranked_results_returns_full_ranked_list_without_diversifying():
    service = _search_service()
    dominant_file = uuid4()
    second_file = uuid4()

    merged = service._merge_ranked_results(
        [
            _result(dominant_file, 0, 0.99),
            _result(dominant_file, 1, 0.98),
            _result(second_file, 0, 0.80),
        ],
        [],
    )

    # Merge no longer caps per file or truncates — that happens after reranking.
    assert [item["file_id"] for item in merged] == [
        dominant_file,
        dominant_file,
        second_file,
    ]


def test_diversify_file_results_caps_chunks_per_file():
    service = _search_service()
    dominant_file = uuid4()
    second_file = uuid4()
    third_file = uuid4()

    ranked = [
        _obj(dominant_file, 0),
        _obj(dominant_file, 1),
        _obj(dominant_file, 2),
        _obj(second_file, 0),
        _obj(third_file, 0),
    ]

    diversified = service._diversify_file_results(ranked, 4)

    assert [item.file_id for item in diversified] == [
        dominant_file,
        dominant_file,
        second_file,
        third_file,
    ]


@pytest.mark.asyncio
async def test_noop_reranker_keeps_first_stage_order():
    reranker = NoopReranker()
    a, b = uuid4(), uuid4()
    results = [_obj(a, 0), _obj(b, 0)]
    out = await reranker.rerank("q", results, top_n=2)
    assert [r.file_id for r in out] == [a, b]


@pytest.mark.asyncio
async def test_index_failure_keeps_prior_search_revision():
    class FailingEmbedder(_FakeEmbedder):
        async def embed_batch(self, texts: list[str]):
            raise RuntimeError("provider unavailable")

    service = _search_service()
    service.embedder = FailingEmbedder()
    service.ensure_schema = AsyncMock()
    service.chunk_repo.add_chunks = AsyncMock()
    service.chunk_repo.remove_chunks_by_file = AsyncMock()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.index_file_chunks(uuid4(), [{"text": "new"}])

    service.chunk_repo.add_chunks.assert_not_awaited()
    service.chunk_repo.remove_chunks_by_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_rejects_embedding_count_mismatch():
    class TruncatingEmbedder(_FakeEmbedder):
        async def embed_batch(self, texts: list[str]):
            return [[0.0]]

    service = _search_service()
    service.embedder = TruncatingEmbedder()
    service.ensure_schema = AsyncMock()
    service.chunk_repo.add_chunks = AsyncMock()

    with pytest.raises(ValueError, match="1 vectors for 2 chunks"):
        await service.index_file_chunks(uuid4(), [{"text": "one"}, {"text": "two"}])

    service.chunk_repo.add_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_reports_schema_embedding_and_persistence_timings():
    service = _search_service()
    service.ensure_schema = AsyncMock()
    service.chunk_repo.add_chunks = AsyncMock()

    result = await service.index_file_chunks(
        uuid4(),
        [{"text": "one"}, {"text": "two"}],
    )

    assert result.chunk_count == 2
    assert result.schema_seconds >= 0
    assert result.embedding_seconds >= 0
    assert result.persistence_seconds >= 0


@pytest.mark.asyncio
async def test_index_reports_zero_work_for_empty_chunk_set():
    service = _search_service()
    service.ensure_schema = AsyncMock()
    service.chunk_repo.add_chunks = AsyncMock()

    result = await service.index_file_chunks(uuid4(), [])

    assert result.chunk_count == 0
    assert result.embedding_seconds == 0
    assert result.persistence_seconds == 0
    service.chunk_repo.add_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_schema_locks_the_same_key_as_the_pod_schema_provisioner():
    """Both creators of one pod schema must serialise on the SAME key.

    ``CREATE SCHEMA IF NOT EXISTS`` is not atomic in PostgreSQL. Two callers
    holding *different* advisory locks exclude nobody: both can observe the
    namespace as absent, and the loser fails the ``pg_namespace.nspname``
    unique index. That is not hypothetical -- it is what broke the datastore
    e2e shard:

        duplicate key value violates unique constraint
        "pg_namespace_nspname_index"
        DETAIL:  Key (nspname)=(pod_01a0289d_...) already exists.
        [SQL: CREATE SCHEMA IF NOT EXISTS "pod_01a0289d_..."]

    ``ensure_schema`` used to take only the global ``_ENSURE_SCHEMA_LOCK_KEY``,
    which serialises the database-wide catalogs and nothing about this pod,
    while ``SchemaManager`` took ``hashtext(schema_name)``.
    """
    pod_id = uuid4()
    connection = AsyncMock()
    connection.scalar.return_value = True
    service = PostgresSearchService(
        pod_id,
        engine=_Engine(connection),
        session_factory=object(),
        embedder=_FakeEmbedder(),
        reranker=NoopReranker(),
    )

    # Each module builds the schema name with its own f-string. Identical lock
    # SQL would still exclude nobody if those two ever drifted apart, so the
    # names are part of the invariant, not an incidental detail.
    manager = SchemaManager.__new__(SchemaManager)
    assert manager.get_schema_name(pod_id) == service.schema_name

    await service.ensure_schema()

    calls = connection.execute.await_args_list
    statements = [str(call.args[0]) for call in calls]
    per_schema_locks = [
        index
        for index, statement in enumerate(statements)
        if "pg_advisory_xact_lock(hashtext(" in statement
    ]
    assert per_schema_locks, (
        "ensure_schema() never locked this pod's schema; the global key alone "
        "does not exclude SchemaManager"
    )
    lock_index = per_schema_locks[0]
    create_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE SCHEMA")
    )
    assert lock_index < create_index, "the lock must be held before CREATE SCHEMA"
    assert calls[lock_index].args[1] == {"schema_name": service.schema_name}

    # The lock is worthless unless it is byte-for-byte the provisioner's key.
    provisioner_connection = AsyncMock()
    await SchemaManager._lock_schema_bootstrap(
        provisioner_connection, service.schema_name
    )
    provisioner_statement = str(provisioner_connection.execute.await_args.args[0])
    assert provisioner_statement == statements[lock_index]


class TestAFailedIndexBuildIsVisible:
    """Production runs at LOG_LEVEL=INFO, so a caught failure logged at debug is
    the same as one that was never logged. A pod whose vector index never built
    keeps answering searches by sequential scan and simply gets slower as it
    grows -- until `guard_query_plan`'s cost ceiling starts refusing unrelated
    queries in the same schema.
    """

    def _engine_that_fails(self, message: str):
        @asynccontextmanager
        async def _begin():
            raise RuntimeError(message)
            yield  # pragma: no cover - makes this an async generator

        return SimpleNamespace(begin=_begin)

    async def _warnings(self, caplog, message: str) -> tuple[list[dict], str]:
        service = _search_service()
        service.engine = self._engine_that_fails(message)
        with caplog.at_level(logging.DEBUG):
            await service._ensure_vector_index()
        return [
            record.msg
            for record in caplog.records
            if record.levelno >= logging.WARNING and isinstance(record.msg, dict)
        ], service.schema_name

    @pytest.mark.asyncio
    async def test_a_refused_build_is_reported_at_warning(self, caplog) -> None:
        warnings, _ = await self._warnings(caplog, "permission denied for schema")

        assert "datastore.postgres_search_service.vector_index_build.degraded" in [
            warning["event"] for warning in warnings
        ]

    @pytest.mark.asyncio
    async def test_the_report_names_the_schema_that_lost_its_index(
        self, caplog
    ) -> None:
        """Without it the line says a build failed somewhere in the install."""
        warnings, schema_name = await self._warnings(
            caplog, "permission denied for schema"
        )

        build = next(
            warning
            for warning in warnings
            if warning["event"].endswith("vector_index_build.degraded")
        )
        assert build["schema_name"] == schema_name
        assert "permission denied for schema" in build["error_traceback"]

    @pytest.mark.asyncio
    async def test_a_missing_extension_stays_quiet(self, caplog) -> None:
        """An install without pgvector is a deployment choice, not a fault, and
        it would otherwise warn once per pod on every process."""
        warnings, _ = await self._warnings(caplog, 'extension "vector" does not exist')

        assert not [
            warning
            for warning in warnings
            if warning["event"].endswith("vector_index_build.degraded")
        ]
