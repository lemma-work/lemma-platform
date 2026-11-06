from __future__ import annotations

from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.modules.datastore.domain.file_entities import DatastoreFileSearchResult
from app.modules.datastore.infrastructure import reranker as reranker_module
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.infrastructure.reranker import NoopReranker
from app.modules.datastore.services.search import postgres_search_service
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


class _RecordingReranker:
    def __init__(self) -> None:
        self.sent: list[int] = []

    async def rerank(self, query, results, *, top_n):
        self.sent.append(len(results))
        return list(reversed(results))[:top_n]


@pytest.mark.asyncio
async def test_hybrid_search_sends_the_reranker_the_pool_not_the_whole_fusion(
    monkeypatch,
):
    """The searcher's overshoot makes `per_side` three times a 50-chunk limit,
    so fusion handed the hosted reranker up to 300 chunks per search, and its
    cost grows with every one. Only the pool, the fused order's best, is sent."""
    monkeypatch.setattr(
        postgres_search_service.datastore_settings, "reranker_mode", "openai_compat"
    )
    monkeypatch.setattr(
        postgres_search_service.datastore_settings, "reranker_retrieve_n", 50
    )
    reranker = _RecordingReranker()
    service = _search_service()
    service.reranker = reranker
    service.schema_version = AsyncMock(return_value=2)
    files = [uuid4() for _ in range(150)]
    service.chunk_repo.vector_search = AsyncMock(
        return_value=[_result(f, 0, 0.9) for f in files]
    )
    service.chunk_repo.text_search = AsyncMock(
        return_value=[_result(f, 1, 0.5) for f in files]
    )

    results = await service.search(
        "budget", limit=50, file_scope=SearchFileScope.post_filtered()
    )

    assert service.chunk_repo.vector_search.await_args.kwargs["limit"] == 150
    assert reranker.sent == [50]
    assert len(results) == 50


@pytest.mark.asyncio
async def test_hosted_reranker_keeps_first_stage_order_past_its_deadline(monkeypatch):
    """A slow reranker used to hold a search for its 60-second client timeout,
    on a connection opened afresh for every call. It now uses the process's
    warm client, under the configured deadline, and a timeout falls back."""
    seen: dict[str, object] = {}

    def slow(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions["timeout"]["read"]
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(
        reranker_module.settings, "lemma_openai_api_key", SecretStr("test-key")
    )
    monkeypatch.setattr(
        reranker_module.datastore_settings, "reranker_timeout_seconds", 3.0
    )
    a, b = uuid4(), uuid4()

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        out = await reranker_module.OpenAICompatReranker(client=client).rerank(
            "q", [_obj(a, 0), _obj(b, 0)], top_n=2
        )

    assert [r.file_id for r in out] == [a, b]
    assert seen["timeout"] == 3.0


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["data", "results"])
async def test_hosted_reranker_reads_either_response_shape(monkeypatch, shape):
    """Fireworks answers `{"data": [...]}`; only `results` was read, so every
    successful rerank there kept the first-stage order."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                shape: [
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.9},
                ]
            },
        )

    monkeypatch.setattr(
        reranker_module.settings, "lemma_openai_api_key", SecretStr("test-key")
    )
    a, b = uuid4(), uuid4()

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        out = await reranker_module.OpenAICompatReranker(client=client).rerank(
            "q", [_obj(a, 0), _obj(b, 0)], top_n=2
        )

    assert [r.file_id for r in out] == [b, a]


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
    # Still a replacement: a revision with no text must not leave the previous
    # revision's chunks answering for it.
    assert service.chunk_repo.add_chunks.await_args.args[1:3] == ([], [])
