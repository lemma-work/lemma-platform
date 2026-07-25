import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.datastore.composition import (
    DatastoreComposition,
    install_datastore_composition,
)
import app.modules.datastore.module as datastore_module
from app.modules.datastore.module import (
    _preload_local_embeddings,
    embedding_capability,
    module,
)


@pytest.mark.asyncio
async def test_embedding_preload_runs_for_enabled_local_worker(monkeypatch):
    embedder = SimpleNamespace(embed=AsyncMock(return_value=[0.0, 1.0]))
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "local_embedding_preload", True)
    monkeypatch.setattr(settings, "local_embedding_startup_mode", "blocking")
    monkeypatch.setattr(settings, "embedding_dimension", 2)
    previous = install_datastore_composition(
        DatastoreComposition(embedder_provider=lambda: embedder)
    )
    try:
        async with _preload_local_embeddings(object()):
            pass
    finally:
        install_datastore_composition(previous)

    embedder.embed.assert_awaited_once()


@pytest.mark.asyncio
async def test_embedding_preload_respects_composition_policy(monkeypatch):
    embedder = SimpleNamespace(embed=AsyncMock())
    previous = install_datastore_composition(
        DatastoreComposition(
            embedder_provider=lambda: embedder,
            preload_embeddings=False,
        )
    )
    try:
        async with _preload_local_embeddings(object()):
            pass
    finally:
        install_datastore_composition(previous)

    embedder.embed.assert_not_awaited()


def test_embedding_preload_is_registered_for_api_and_worker_startup():
    assert _preload_local_embeddings in module.api_lifespans
    assert _preload_local_embeddings in module.worker_lifespans


@pytest.mark.asyncio
async def test_background_embedding_preload_does_not_block_startup(monkeypatch):
    release = asyncio.Event()

    async def embed(_text):
        await release.wait()
        return [0.0, 1.0]

    embedder = SimpleNamespace(embed=AsyncMock(side_effect=embed))
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "local_embedding_preload", True)
    monkeypatch.setattr(settings, "local_embedding_startup_mode", "background")
    monkeypatch.setattr(settings, "embedding_dimension", 2)
    monkeypatch.setattr(datastore_module, "_embedding_init_task", None)
    previous = install_datastore_composition(
        DatastoreComposition(embedder_provider=lambda: embedder)
    )
    try:
        async with _preload_local_embeddings(object()):
            await asyncio.sleep(0)
            assert embedding_capability().status == "preparing"
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert embedding_capability().status == "ready"
    finally:
        install_datastore_composition(previous)


@pytest.mark.asyncio
async def test_background_embedding_failure_degrades_without_raising(monkeypatch):
    embedder = SimpleNamespace(embed=AsyncMock(side_effect=ConnectionError("offline")))
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "local_embedding_preload", True)
    monkeypatch.setattr(settings, "local_embedding_startup_mode", "background")
    monkeypatch.setattr(datastore_module, "_embedding_init_task", None)
    previous = install_datastore_composition(
        DatastoreComposition(embedder_provider=lambda: embedder)
    )
    try:
        async with _preload_local_embeddings(object()):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert embedding_capability().status == "degraded"
    finally:
        install_datastore_composition(previous)
