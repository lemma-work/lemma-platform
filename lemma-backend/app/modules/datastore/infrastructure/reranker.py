"""``RerankerPort`` adapters — an optional second stage that reorders first-stage
(hybrid) search results by cross-encoder relevance.

Three env-selected backends (``RERANKER_MODE``):
- ``off``          → :class:`NoopReranker` (first-stage order kept; zero cost)
- ``local``        → :class:`LocalCrossEncoderReranker` (CPU cross-encoder)
- ``openai_compat``→ :class:`OpenAICompatReranker` (``/rerank`` on any
                      OpenAI-compatible endpoint; requires LEMMA_OPENAI_BASE_URL
                      + LEMMA_OPENAI_API_KEY)

Reranking is best-effort: any backend failure (missing optional dep, network
error) falls back to the first-stage order so search never breaks.
"""

from __future__ import annotations

import httpx

from app.core.config import reveal_secret, settings
from app.core.log.log import get_logger
from app.modules.datastore.domain.file_entities import DatastoreFileSearchResult

logger = get_logger(__name__)


class NoopReranker:
    async def rerank(
        self,
        query: str,
        results: list[DatastoreFileSearchResult],
        *,
        top_n: int,
    ) -> list[DatastoreFileSearchResult]:
        return list(results)[:top_n]


class LocalCrossEncoderReranker:
    """Local CPU cross-encoder (default ``BAAI/bge-reranker-v2-m3``) via
    sentence-transformers. The model is loaded lazily on first use."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.local_reranker_model
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder  # optional dependency

            self._model = CrossEncoder(self.model_name)
        return self._model

    async def rerank(
        self,
        query: str,
        results: list[DatastoreFileSearchResult],
        *,
        top_n: int,
    ) -> list[DatastoreFileSearchResult]:
        if not results:
            return []
        try:
            import anyio

            model = self._load_model()
            pairs = [(query, result.content or "") for result in results]
            scores = await anyio.to_thread.run_sync(lambda: model.predict(pairs))
            order = sorted(
                range(len(results)),
                key=lambda index: float(scores[index]),
                reverse=True,
            )
            return [results[index] for index in order[:top_n]]
        except Exception as exc:
            logger.warning("Local reranker failed; keeping first-stage order: %s", exc)
            return list(results)[:top_n]


class OpenAICompatReranker:
    """OpenAI-compatible hosted reranker over the ``/rerank`` endpoint.

    Uses LEMMA_OPENAI_BASE_URL + LEMMA_OPENAI_API_KEY — the same credentials
    already used for the LLM system profile. Point LEMMA_OPENAI_BASE_URL at
    any provider that exposes a ``/rerank`` endpoint (e.g. Fireworks).
    """

    def __init__(self, model: str | None = None):
        self.model = model or settings.openai_compat_reranker_model

    async def rerank(
        self,
        query: str,
        results: list[DatastoreFileSearchResult],
        *,
        top_n: int,
    ) -> list[DatastoreFileSearchResult]:
        if not results:
            return []
        api_key = reveal_secret(settings.lemma_openai_api_key)
        if not api_key:
            logger.warning(
                "openai_compat reranking requires LEMMA_OPENAI_API_KEY; "
                "keeping first-stage order."
            )
            return list(results)[:top_n]
        url = f"{settings.lemma_openai_base_url.rstrip('/')}/rerank"
        documents = [result.content or "" for result in results]
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": documents,
                        "top_n": top_n,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning(
                "openai_compat reranker failed; keeping first-stage order: %s", exc
            )
            return list(results)[:top_n]

        ranked: list[DatastoreFileSearchResult] = []
        for item in payload.get("results", []):
            index = item.get("index")
            if index is None or index >= len(results):
                continue
            ranked.append(results[index])
        return ranked[:top_n] if ranked else list(results)[:top_n]


def create_reranker():
    """Composition helper — pick the reranker adapter from ``RERANKER_MODE``."""
    mode = settings.reranker_mode
    if mode == "local":
        return LocalCrossEncoderReranker()
    if mode == "openai_compat":
        return OpenAICompatReranker()
    return NoopReranker()
