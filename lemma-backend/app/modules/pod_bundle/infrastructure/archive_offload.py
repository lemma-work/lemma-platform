"""Inflating a pod bundle without stopping the worker."""

from __future__ import annotations

from pathlib import Path

from app.core.concurrency.offload import run_blocking
from app.modules.pod_bundle.config import pod_bundle_settings


async def extract_bundle_offloaded(archive: bytes, destination: str) -> Path:
    """Inflate ``archive`` into ``destination`` in a worker thread.

    It writes every member to disk, bounded only by
    ``pod_bundle_max_uncompressed_bytes``. Run inline it stops the worker's
    event loop for the whole extraction -- including the cancellation checks
    the import loop makes around it.
    """
    from lemma_pod_bundle import extract_bundle

    return await run_blocking(
        extract_bundle,
        archive,
        Path(destination),
        max_uncompressed_bytes=pod_bundle_settings.pod_bundle_max_uncompressed_bytes,
        limiter="cpu_bound",
    )
