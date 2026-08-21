from __future__ import annotations

from pathlib import Path
import asyncio
import threading
import time

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.embeddings.embeddings import Embedder
from app.core.embeddings.factory import create_embedder
from app.core.embeddings.local_embedder import FastEmbedLocalEmbedder
from app.modules.test_support.embeddings import DeterministicTestEmbedder
from app.modules.datastore.infrastructure.storage import (
    AzureDatastoreStorage,
    GCSDatastoreStorage,
    LocalDatastoreStorage,
    S3DatastoreStorage,
    create_datastore_storage,
)


def test_local_environment_uses_local_storage_and_embeddings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "storage_backend", "auto")
    monkeypatch.setattr(settings, "embedding_provider", "auto")
    monkeypatch.setattr(settings, "local_object_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "storage_bucket", str(tmp_path / "storage"))

    assert settings.effective_storage_backend() == "local"
    assert isinstance(create_datastore_storage(), LocalDatastoreStorage)
    assert settings.effective_embedding_provider() == "local"
    assert isinstance(create_embedder(), Embedder)
    assert create_embedder() is create_embedder()


@pytest.mark.asyncio
async def test_fastembed_local_embedder_normalizes_model_vectors():
    class FakeFastEmbed:
        def embed(self, texts, **kwargs):
            assert kwargs["batch_size"] == 32
            return [[1.0, 0.5, -0.25] for _ in texts]

    embedder = FastEmbedLocalEmbedder(
        dimension=3,
        model=FakeFastEmbed(),
    )

    first = await embedder.embed("local embeddings are local")
    batch = await embedder.embed_batch(["one", "two"])

    assert first == [1.0, 0.5, -0.25]
    assert batch == [[1.0, 0.5, -0.25], [1.0, 0.5, -0.25]]


@pytest.mark.asyncio
async def test_fastembed_local_embedder_rejects_wrong_dimension():
    class FakeFastEmbed:
        def embed(self, texts, **kwargs):
            return [[1.0, 0.5] for _ in texts]

    embedder = FastEmbedLocalEmbedder(
        dimension=3,
        model=FakeFastEmbed(),
    )

    with pytest.raises(ValueError, match="expected 3"):
        await embedder.embed("too short")


@pytest.mark.asyncio
async def test_fastembed_local_embedder_rejects_missing_vectors():
    class TruncatingFastEmbed:
        def embed(self, texts, **kwargs):
            return [[1.0, 0.5, -0.25]]

    embedder = FastEmbedLocalEmbedder(dimension=3, model=TruncatingFastEmbed())

    with pytest.raises(ValueError, match="1 vectors for 2 texts"):
        await embedder.embed_batch(["one", "two"])


@pytest.mark.asyncio
async def test_fastembed_model_initializes_once_under_concurrent_first_use(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls = 0

    class FakeTextEmbedding:
        # `threads` mirrors fastembed's real signature — we pin ONNX's intra-op
        # pool so it sizes to the container's CPU limit, not the host's.
        def __init__(self, *, model_name, cache_dir, threads=None):
            nonlocal calls
            calls += 1
            assert Path(cache_dir) == tmp_path

        def embed(self, texts, **kwargs):
            return [[1.0, 0.5, -0.25] for _ in texts]

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    embedder = FastEmbedLocalEmbedder(dimension=3, cache_dir=tmp_path)

    await asyncio.gather(*(embedder.embed(f"text-{index}") for index in range(8)))

    assert calls == 1


@pytest.mark.asyncio
async def test_fastembed_serializes_multi_core_inference_calls():
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    class FakeTextEmbedding:
        def embed(self, texts, **kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return [[1.0, 0.5, -0.25] for _ in texts]

    embedder = FastEmbedLocalEmbedder(
        dimension=3,
        model=FakeTextEmbedding(),
    )

    await asyncio.gather(
        embedder.embed_batch(["one"]),
        embedder.embed_batch(["two"]),
    )

    assert max_active == 1


@pytest.mark.asyncio
async def test_fastembed_repairs_missing_onnx_from_registered_alternate_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repaired = tmp_path / "alternate-source" / "model"
    constructor_calls: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)
            if "specific_model_path" not in kwargs:
                raise RuntimeError("NO_SUCHFILE: model file doesn't exist")

        @staticmethod
        def list_supported_models():
            return [
                {
                    "model": "BAAI/bge-base-en-v1.5",
                    "sources": {
                        "url": "https://models.example/model.tar.gz",
                        "_deprecated_tar_struct": True,
                    },
                }
            ]

        @staticmethod
        def retrieve_model_gcs(model_name, url, cache_dir, **kwargs):
            assert model_name == "BAAI/bge-base-en-v1.5"
            assert url == "https://models.example/model.tar.gz"
            assert Path(cache_dir) == tmp_path
            assert kwargs["deprecated_tar_struct"] is True
            repaired.mkdir(parents=True)
            return repaired

        def embed(self, texts, **kwargs):
            return [[1.0, 0.5, -0.25] for _ in texts]

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    embedder = FastEmbedLocalEmbedder(
        model_name="BAAI/bge-base-en-v1.5",
        dimension=3,
        cache_dir=tmp_path,
    )

    assert await embedder.embed("repaired") == [1.0, 0.5, -0.25]
    assert len(constructor_calls) == 2
    assert constructor_calls[-1]["specific_model_path"] == str(repaired)


@pytest.mark.asyncio
async def test_fastembed_delegates_alternate_cache_reuse_to_library(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    existing = tmp_path / "fast-bge-base-en-v1.5"
    existing.mkdir()
    (existing / "model_optimized.onnx").write_bytes(b"onnx")
    constructor_calls: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)
            if "specific_model_path" not in kwargs:
                raise RuntimeError("NO_SUCHFILE: model file doesn't exist")

        @staticmethod
        def list_supported_models():
            return [
                {
                    "model": "BAAI/bge-base-en-v1.5",
                    "sources": {
                        "url": "https://models.example/model.tar.gz",
                        "_deprecated_tar_struct": True,
                    },
                }
            ]

        @staticmethod
        def retrieve_model_gcs(model_name, url, cache_dir, **kwargs):
            assert model_name == "BAAI/bge-base-en-v1.5"
            assert Path(cache_dir) == tmp_path
            return existing

        def embed(self, texts, **kwargs):
            return [[1.0, 0.5, -0.25] for _ in texts]

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    embedder = FastEmbedLocalEmbedder(
        model_name="BAAI/bge-base-en-v1.5",
        dimension=3,
        cache_dir=tmp_path,
    )

    assert await embedder.embed("cached") == [1.0, 0.5, -0.25]
    assert len(constructor_calls) == 2
    assert constructor_calls[-1]["specific_model_path"] == str(existing)


@pytest.mark.asyncio
async def test_deterministic_test_embedder_is_stable_and_dimensioned():
    embedder = DeterministicTestEmbedder(16)

    first, second = await embedder.embed_batch(["alpha beta", "alpha beta"])

    assert first == second
    assert len(first) == 16
    assert any(first)


@pytest.mark.parametrize(
    ("backend", "storage_type"),
    [
        ("gcs", GCSDatastoreStorage),
        ("s3", S3DatastoreStorage),
        ("azure", AzureDatastoreStorage),
    ],
)
def test_explicit_cloud_backend_selects_datastore_adapter(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    storage_type: type,
):
    from obstore.store import MemoryStore

    from app.modules.datastore.infrastructure import storage as storage_mod

    monkeypatch.setattr(settings, "storage_backend", backend)
    monkeypatch.setattr(settings, "storage_bucket", "cloud-bucket")
    monkeypatch.setattr(storage_mod, "build_object_store", lambda **_: MemoryStore())

    assert settings.effective_storage_backend() == backend
    assert isinstance(create_datastore_storage(), storage_type)


def test_production_auto_with_legacy_bucket_still_uses_gcs(
    monkeypatch: pytest.MonkeyPatch,
):
    from obstore.store import MemoryStore

    from app.modules.datastore.infrastructure import storage as storage_mod

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "storage_backend", "auto")
    monkeypatch.setattr(settings, "storage_bucket", "cloud-bucket")
    monkeypatch.setattr(storage_mod, "build_object_store", lambda **_: MemoryStore())

    assert settings.effective_storage_backend() == "gcs"
    assert isinstance(create_datastore_storage(), GCSDatastoreStorage)


@pytest.mark.asyncio
async def test_local_datastore_storage_round_trips_with_obstore(tmp_path: Path):
    storage = LocalDatastoreStorage(tmp_path)

    assert await storage.upload_file("pod/file.txt", b"hello") is True
    assert await storage.stat_file("pod/file.txt") == 5
    assert await storage.download_file("pod/file.txt") == b"hello"
    assert await storage.copy_file("pod/file.txt", "pod/copied.txt") is True
    assert await storage.download_file("pod/copied.txt") == b"hello"
    assert await storage.delete_prefix("pod") == 2
    assert not (tmp_path / "pod" / "file.txt").exists()


@pytest.mark.asyncio
async def test_download_missing_object_raises_typed_not_found(tmp_path: Path):
    from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError

    storage = LocalDatastoreStorage(tmp_path)

    with pytest.raises(DatastoreObjectNotFoundError):
        await storage.download_file("pod/does-not-exist.txt")


# --- Embedding memory + threading bounds ----------------------------------
#
# Both of these came out of the 100-paper benchmark: the ingestion worker was
# OOM-killed embedding a 533-chunk document in one call, and embedding ran ~3.7x
# slower inside the loaded worker than in isolation because ONNX sized its thread
# pool from the host's CPU count rather than the container's limit.


@pytest.mark.asyncio
async def test_embed_batch_is_sliced_so_peak_memory_is_bounded_by_slice():
    """A long document must not be embedded in one giant call.

    One 95-page paper in the benchmark corpus produced 533 chunks. Embedding
    them together holds every input string and output vector live at once on top
    of the ONNX arena, which is what exhausted the worker's memory limit.
    """
    calls: list[int] = []

    class FakeFastEmbed:
        def embed(self, texts, **kwargs):
            texts = list(texts)
            calls.append(len(texts))
            return [[1.0, 0.0] for _ in texts]

    embedder = FastEmbedLocalEmbedder(dimension=2, model=FakeFastEmbed())
    embedder_slice = 64
    with patch.object(settings, "local_embedding_max_texts_per_call", embedder_slice):
        vectors = await embedder.embed_batch([f"chunk {i}" for i in range(533)])

    # Every vector is still returned, in order, exactly once.
    assert len(vectors) == 533
    assert sum(calls) == 533
    # ...but no single call ever saw the whole document.
    assert max(calls) <= embedder_slice


@pytest.mark.asyncio
async def test_slicing_preserves_vector_order():
    """Slicing must not reorder results — chunk N's vector must stay chunk N's."""

    class FakeFastEmbed:
        def embed(self, texts, **kwargs):
            # Encode the input's identity into the vector so order is checkable.
            return [[float(int(t.split()[-1])), 0.0] for t in texts]

    embedder = FastEmbedLocalEmbedder(dimension=2, model=FakeFastEmbed())
    with patch.object(settings, "local_embedding_max_texts_per_call", 7):
        vectors = await embedder.embed_batch([f"chunk {i}" for i in range(50)])

    assert [int(v[0]) for v in vectors] == list(range(50))


def test_onnx_thread_count_is_pinned_to_the_configured_allocation():
    """Unset, ONNX reads the HOST's CPU count and oversubscribes the cgroup."""
    embedder = FastEmbedLocalEmbedder(dimension=2, model=object())
    with patch.object(settings, "local_embedding_threads", 4):
        assert embedder._threading_kwargs() == {"threads": 4}
    # 0 means "let ONNX decide", which is only right when we own the machine.
    with patch.object(settings, "local_embedding_threads", 0):
        assert embedder._threading_kwargs() == {}
