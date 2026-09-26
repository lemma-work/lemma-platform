"""The local search model's readiness, and how its failures are told apart.

The model is downloaded once, on first use. A failure there says nothing about
the document being indexed, so it must surface as
``EmbeddingModelUnavailableError`` (refunded, retried after a cooldown) --
except an unknown model name, which will never succeed and must say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.embeddings import local_embedder
from app.core.embeddings.local_embedder import (
    EmbeddingModelMisconfiguredError,
    EmbeddingModelUnavailableError,
    FastEmbedLocalEmbedder,
)

pytestmark = pytest.mark.unit


def _install(monkeypatch: pytest.MonkeyPatch, text_embedding) -> None:
    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", text_embedding)


@pytest.mark.asyncio
async def test_a_failed_download_is_unavailable_not_a_document_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    class Offline:
        def __init__(self, **_kwargs):
            raise ConnectionError("could not reach huggingface.co")

    _install(monkeypatch, Offline)
    embedder = FastEmbedLocalEmbedder(dimension=3, cache_dir=tmp_path)
    assert embedder.readiness().status == "idle"

    with pytest.raises(EmbeddingModelUnavailableError):
        await embedder.embed_batch(["one"])

    readiness = embedder.readiness()
    assert readiness.status == "failed"
    assert readiness.error_type == "ConnectionError"
    assert readiness.seconds_until_retry() > 0


@pytest.mark.asyncio
async def test_a_failed_download_is_not_retried_by_every_waiting_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    attempts = 0

    class Offline:
        def __init__(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise OSError("network is unreachable")

    _install(monkeypatch, Offline)
    embedder = FastEmbedLocalEmbedder(dimension=3, cache_dir=tmp_path)

    for _ in range(3):
        with pytest.raises(EmbeddingModelUnavailableError):
            await embedder.embed_batch(["one"])

    assert attempts == 1


@pytest.mark.asyncio
async def test_the_model_loads_after_the_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    online = False

    class Flaky:
        def __init__(self, **_kwargs):
            if not online:
                raise ConnectionError("offline")

        def embed(self, texts, **_kwargs):
            return [[1.0, 0.0, 0.0] for _ in texts]

    _install(monkeypatch, Flaky)
    embedder = FastEmbedLocalEmbedder(dimension=3, cache_dir=tmp_path)
    with pytest.raises(EmbeddingModelUnavailableError):
        await embedder.embed_batch(["one"])

    online = True
    monkeypatch.setattr(local_embedder, "LOCAL_MODEL_RETRY_COOLDOWN_SECONDS", 0.0)

    assert await embedder.embed_batch(["one"]) == [[1.0, 0.0, 0.0]]
    assert embedder.readiness().status == "ready"


@pytest.mark.asyncio
async def test_an_unknown_model_name_is_misconfiguration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    class Unknown:
        def __init__(self, **_kwargs):
            raise ValueError("Model not/a-model is not supported in TextEmbedding")

    _install(monkeypatch, Unknown)
    embedder = FastEmbedLocalEmbedder(
        model_name="not/a-model", dimension=3, cache_dir=tmp_path
    )

    with pytest.raises(EmbeddingModelMisconfiguredError):
        await embedder.embed_batch(["one"])
