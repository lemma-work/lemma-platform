"""Embedding test doubles kept outside production adapter modules."""

from __future__ import annotations

import hashlib

from app.core.embeddings.embeddings import Embedder


class DeterministicTestEmbedder(Embedder):
    """Dependency-free lexical embeddings for hermetic tests."""

    def __init__(self, dimension: int):
        self.dimension = dimension

    async def embed(self, text: str) -> list[float]:
        return self._encode(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(text) for text in texts]

    def _encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        if not any(vector):
            vector[0] = 1.0
        return vector
