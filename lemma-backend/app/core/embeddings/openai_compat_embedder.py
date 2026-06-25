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

from typing import List

import httpx

from app.core.config import reveal_secret, settings
from app.core.embeddings.embeddings import Embedder


class OpenAICompatEmbedder(Embedder):
    BATCH_SIZE = 50

    def __init__(self, model: str | None = None, dimension: int | None = None):
        self.model = model or settings.openai_compat_embedding_model
        self.dimension = dimension or settings.embedding_dimension

    async def embed(self, text: str) -> List[float]:
        embeddings = await self.embed_batch([text])
        return embeddings[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
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
        async with httpx.AsyncClient(timeout=60.0) as client:
            for i in range(0, len(texts), self.BATCH_SIZE):
                batch = texts[i : i + self.BATCH_SIZE]
                try:
                    response = await client.post(
                        url,
                        headers=headers,
                        json={
                            "model": self.model,
                            "input": batch,
                            "dimensions": self.dimension,
                        },
                    )
                    response.raise_for_status()
                except Exception as e:
                    raise Exception(
                        f"Failed to get embeddings for batch starting at index {i}: {e}"
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
