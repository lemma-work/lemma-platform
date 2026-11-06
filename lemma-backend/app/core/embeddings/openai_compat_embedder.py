"""OpenAI-compatible embedding provider (standard /embeddings endpoint).

Reuses the server-provided Lemma OpenAI-compatible credentials
(``lemma_openai_api_key`` / ``lemma_openai_base_url``), which already point at
any OpenAI-compatible embedding endpoint. Point LEMMA_OPENAI_BASE_URL at the
provider of your choice (Fireworks, a local server, a gateway, etc.).

The default model (``nomic-ai/nomic-embed-text-v1.5``) is 768-dim and supports
Matryoshka ``dimensions``, matching the existing ``embedding_dimension`` without
a schema change.
"""

from __future__ import annotations

import asyncio
import random
from typing import List

import httpx

from app.core.config import reveal_secret, settings
from app.core.embeddings.embeddings import Embedder, EmbeddingProviderError
from app.core.net.http_client import get_shared_http_client
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Attempts per batch, including the first. Bounded deliberately: the caller is
#: a background job with its own retry, and a provider that is still refusing
#: after this long is having an outage rather than a blip.
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 8.0
#: Per request. A batch of fifty document chunks is the slow case; the shared
#: client's own read timeout is sized for connector calls.
_REQUEST_TIMEOUT_SECONDS = 60.0
#: Per attempt, for one short text -- a search query, which a person is waiting
#: on. A provider that has not answered one sentence in this long is stuck on
#: that request, and the retry below gets a fresh one rather than sitting out
#: the batch timeout.
_SINGLE_TEXT_TIMEOUT_SECONDS = 10.0


def _is_worth_retrying(exc: Exception) -> bool:
    """Whether asking again could plausibly get a different answer.

    A 429 and a 5xx are the provider saying "not now"; a transport error never
    reached it at all. Every other 4xx is a statement about the request, and a
    bad request does not become a good one by being sent four times.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _backoff_seconds(attempt: int) -> float:
    """Exponential, with jitter so concurrent workers do not retry in lockstep."""
    delay = min(_BASE_BACKOFF_SECONDS * (2**attempt), _MAX_BACKOFF_SECONDS)
    return delay * random.uniform(0.8, 1.2)


class OpenAICompatEmbedder(Embedder):
    BATCH_SIZE = 50

    def __init__(self, model: str | None = None, dimension: int | None = None):
        self.model = model or settings.openai_compat_embedding_model
        self.dimension = dimension or settings.embedding_dimension

    async def embed(self, text: str) -> List[float]:
        embeddings = await self._embed(
            [text], attempt_timeout=_SINGLE_TEXT_TIMEOUT_SECONDS
        )
        return embeddings[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return await self._embed(texts, attempt_timeout=_REQUEST_TIMEOUT_SECONDS)

    async def _embed(
        self, texts: List[str], *, attempt_timeout: float
    ) -> List[List[float]]:
        if not texts:
            return []

        api_key = reveal_secret(settings.lemma_openai_api_key)
        if not api_key:
            raise RuntimeError(
                "OpenAI-compatible embeddings require LEMMA_OPENAI_API_KEY to be set "
                "(or set EMBEDDING_PROVIDER=local to use offline embeddings)."
            )
        url = f"{settings.lemma_openai_base_url.rstrip('/')}/embeddings"
        headers = {"Authorization": f"Bearer {api_key}"}

        all_embeddings: List[List[float]] = []
        # The process-wide client, not one per call: a search embeds its query
        # before it can read anything, and a fresh client paid a new TCP and
        # TLS handshake to the provider on every one of them.
        client = get_shared_http_client()
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            response = await self._post_batch(
                client, url, headers, batch, index=i, timeout=attempt_timeout
            )
            # OpenAI-shaped response: {"data": [{"embedding": [...], "index": n}]}.
            # Sort by index so the order matches the input batch.
            data = sorted(
                response.json().get("data", []),
                key=lambda item: item.get("index", 0),
            )
            for item in data:
                vector = [float(value) for value in item.get("embedding", [])]
                if len(vector) != self.dimension:
                    raise ValueError(
                        f"Embedding model {self.model!r} returned "
                        f"{len(vector)} dimensions; expected {self.dimension}. "
                        "Set EMBEDDING_DIMENSION to match the model."
                    )
                all_embeddings.append(vector)
        return all_embeddings

    async def _post_batch(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        batch: List[str],
        *,
        index: int,
        timeout: float,
    ) -> httpx.Response:
        """One batch, retried while the provider is only temporarily unwilling.

        Without this a single `503` from the embedding endpoint failed the whole
        datastore file job -- the document was already parsed, chunked and
        stored, and every one of those chunks was thrown away because one HTTP
        call landed during an upstream blip.
        """
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await client.post(
                    url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "input": batch,
                        "dimensions": self.dimension,
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                return response
            # Narrow on purpose: `post` and `raise_for_status` between them
            # raise nothing else, and catching `Exception` here would report a
            # defect in this module as the provider being unwell.
            except httpx.HTTPError as exc:
                last_attempt = attempt == _MAX_ATTEMPTS - 1
                if last_attempt or not _is_worth_retrying(exc):
                    raise EmbeddingProviderError(
                        f"Failed to get embeddings for batch starting at index "
                        f"{index} after {attempt + 1} attempt"
                        f"{'' if attempt == 0 else 's'}: {exc}"
                    ) from exc
                delay = _backoff_seconds(attempt)
                logger.warning(
                    "embeddings.provider.retrying.degraded",
                    attempt=attempt + 1,
                    max_attempts=_MAX_ATTEMPTS,
                    delay_seconds=round(delay, 2),
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable: the loop either returns or raises")
