from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.embeddings import factory
from app.modules.datastore.module import _preload_local_embeddings


@pytest.mark.asyncio
async def test_embedding_preload_runs_for_enabled_local_worker(monkeypatch):
    embedder = SimpleNamespace(embed=AsyncMock(return_value=[0.0, 1.0]))
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "local_embedding_preload", True)
    monkeypatch.setattr(settings, "embedding_dimension", 2)
    monkeypatch.setattr(factory, "create_embedder", lambda: embedder)

    async with _preload_local_embeddings(object()):
        pass

    embedder.embed.assert_awaited_once()


@pytest.mark.asyncio
async def test_embedding_preload_skips_regular_testing_workers(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "e2e_deterministic_embeddings", False)
    monkeypatch.setattr(factory, "create_embedder", create)

    async with _preload_local_embeddings(object()):
        pass

    create.assert_not_awaited()
