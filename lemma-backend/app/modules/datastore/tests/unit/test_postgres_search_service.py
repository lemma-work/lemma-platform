from __future__ import annotations

from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.modules.datastore.domain.file_entities import DatastoreFileSearchResult
from app.modules.datastore.infrastructure.reranker import NoopReranker
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
