"""Local embedding providers for development and test environments."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, List

import anyio
from filelock import FileLock

from app.core.config import settings
from app.core.embeddings.embeddings import Embedder
from app.core.log.log import get_logger

logger = get_logger(__name__)


class FastEmbedLocalEmbedder(Embedder):
    """CPU-only local semantic embeddings backed by FastEmbed/ONNX."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        dimension: int | None = None,
        batch_size: int = 32,
        cache_dir: str | Path | None = None,
        model: Any | None = None,
    ):
        self.model_name = model_name or settings.local_embedding_model
        self.dimension = dimension or settings.embedding_dimension
        self.batch_size = batch_size
        self.cache_dir = Path(
            cache_dir or settings.local_embedding_cache_dir
        ).expanduser()
        self._model = model
        self._model_lock = Lock()
        # ONNX sessions use their own multi-core thread pools. Concurrent calls
        # on the same local model oversubscribe the CPU, increase per-file
        # latency, and can starve the API event loop in standalone mode.
        self._inference_lock = Lock()

    async def embed(self, text: str) -> List[float]:
        embeddings = await self.embed_batch([text])
        return embeddings[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await anyio.to_thread.run_sync(self._encode_batch, list(texts))

    def _load_model(self):
        if self._model is not None:
            return self._model

        # The lru-cached factory normally gives a process one embedder, but a
        # burst of worker tasks can still race on its first use. The thread lock
        # protects this instance and the file lock protects multiple worker
        # processes sharing the persistent model cache.
        with self._model_lock:
            if self._model is not None:
                return self._model
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            lock_path = self.cache_dir / ".lemma-fastembed-init.lock"
            with FileLock(str(lock_path)):
                from fastembed import TextEmbedding

                try:
                    self._model = TextEmbedding(
                        model_name=self.model_name,
                        cache_dir=str(self.cache_dir),
                    )
                except Exception as exc:
                    if not self._is_missing_model_artifact(exc):
                        raise
                    self._model = self._load_registered_alternate(TextEmbedding, exc)
        return self._model

    @staticmethod
    def _is_missing_model_artifact(exc: Exception) -> bool:
        message = str(exc).lower()
        return isinstance(exc, FileNotFoundError) or (
            "nosuchfile" in message
            or "no_suchfile" in message
            or ("model" in message and "file doesn't exist" in message)
        )

    def _load_registered_alternate(self, text_embedding_type, exc: Exception):
        """Let FastEmbed resolve/cache its registered alternate model source.

        FastEmbed normally tries its Hugging Face source and registered URL in
        order. A Hub snapshot can occasionally resolve even though its ONNX
        artifact is absent, failing during session construction before the
        library reaches that alternate. We select the same registered source,
        but leave download validation, atomic placement, and cache reuse entirely
        to FastEmbed's model manager.
        """
        description = next(
            (
                item
                for item in text_embedding_type.list_supported_models()
                if item.get("model") == self.model_name
            ),
            None,
        )
        sources = (description or {}).get("sources") or {}
        alternate_url = sources.get("url")
        if not alternate_url:
            raise exc

        logger.warning(
            "FastEmbed's Hugging Face model is missing its ONNX artifact; "
            "using FastEmbed's registered alternate cache/source for %s",
            self.model_name,
        )
        repaired_path = text_embedding_type.retrieve_model_gcs(
            self.model_name,
            str(alternate_url),
            str(self.cache_dir),
            deprecated_tar_struct=bool(sources.get("_deprecated_tar_struct")),
            local_files_only=False,
        )
        return text_embedding_type(
            model_name=self.model_name,
            cache_dir=str(self.cache_dir),
            specific_model_path=str(repaired_path),
        )

    def _encode_batch(self, texts: list[str]) -> list[list[float]]:
        with self._inference_lock:
            model = self._load_model()
            raw_embeddings = list(model.embed(texts, batch_size=self.batch_size))
        if len(raw_embeddings) != len(texts):
            raise ValueError(
                f"Local embedding model {self.model_name!r} returned "
                f"{len(raw_embeddings)} vectors for {len(texts)} texts"
            )
        vectors = [
            [float(value) for value in self._as_list(vector)]
            for vector in raw_embeddings
        ]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Local embedding model {self.model_name!r} returned "
                    f"{len(vector)} dimensions; expected {self.dimension}. "
                    "Set EMBEDDING_DIMENSION to match the model."
                )
        return vectors

    def _as_list(self, vector: Any) -> list[float]:
        if hasattr(vector, "tolist"):
            return vector.tolist()
        return list(vector)
