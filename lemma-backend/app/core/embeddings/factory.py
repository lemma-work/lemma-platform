"""Embedding provider selection."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.embeddings.embeddings import Embedder
from app.core.embeddings.local_embedder import FastEmbedLocalEmbedder


def create_embedder() -> Embedder:
    return _create_embedder(
        settings.effective_embedding_provider(),
        settings.local_embedding_model,
        settings.local_embedding_cache_dir,
        settings.openai_compat_embedding_model,
        settings.embedding_dimension,
    )


@lru_cache(maxsize=8)
def _create_embedder(
    provider: str,
    local_model: str,
    local_cache_dir: str,
    openai_compat_model: str,
    dimension: int,
) -> Embedder:
    if provider == "openai_compat":
        from app.core.embeddings.openai_compat_embedder import OpenAICompatEmbedder

        return OpenAICompatEmbedder(model=openai_compat_model, dimension=dimension)
    return FastEmbedLocalEmbedder(
        model_name=local_model,
        dimension=dimension,
        cache_dir=local_cache_dir,
    )
