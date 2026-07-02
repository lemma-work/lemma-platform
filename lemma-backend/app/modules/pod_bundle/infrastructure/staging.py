"""Object-storage staging for bundle archives.

Staged archives live under ``{staging_prefix}/pod-imports/{import_id}/bundle.zip``
and ``…/pod-exports/{export_id}/bundle.zip`` so the API process (which accepts
an upload) and the worker process (which plans/applies) can be separate
replicas. Redis holds state JSON only — blobs never go there — and archives
are removed on job completion with the sweep cron as backstop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal
from uuid import UUID

import obstore as obs
from obstore.store import ObjectStore

from app.core.config import settings
from app.core.log.log import get_logger
from app.core.object_storage import build_object_store, local_object_storage_path
from app.modules.pod_bundle.config import pod_bundle_settings

logger = get_logger(__name__)

StagingKind = Literal["pod-imports", "pod-exports"]


def archive_key(kind: StagingKind, job_id: UUID) -> str:
    return f"{kind}/{job_id}/bundle.zip"


class BundleStagingStorage:
    def __init__(self, store: ObjectStore | None = None):
        self.store = store or build_object_store(
            local_prefix=local_object_storage_path(
                pod_bundle_settings.pod_bundle_staging_prefix
            ),
            bucket_name=settings.gcs_storage_bucket,
        )

    async def put_archive(self, kind: StagingKind, job_id: UUID, data: bytes) -> str:
        key = archive_key(kind, job_id)
        await obs.put_async(self.store, key, data)
        return key

    async def get_archive(self, kind: StagingKind, job_id: UUID) -> bytes | None:
        """Full archive bytes, or ``None`` when swept/absent (the caller maps
        that to the 410 staging-missing condition)."""
        try:
            response = await obs.get_async(self.store, archive_key(kind, job_id))
            data = await response.bytes_async()
            return data.to_bytes()
        except Exception as exc:
            if _is_missing_object_error(exc):
                return None
            raise

    async def iter_archive(
        self, kind: StagingKind, job_id: UUID
    ) -> AsyncIterator[bytes] | None:
        """Chunked stream for download endpoints, or ``None`` when absent."""
        try:
            response = await obs.get_async(self.store, archive_key(kind, job_id))
        except Exception as exc:
            if _is_missing_object_error(exc):
                return None
            raise

        async def iterator() -> AsyncIterator[bytes]:
            async for chunk in response.stream():
                yield bytes(chunk)

        return iterator()

    async def delete_archive(self, kind: StagingKind, job_id: UUID) -> None:
        try:
            await obs.delete_async(self.store, archive_key(kind, job_id))
        except Exception as exc:
            if _is_missing_object_error(exc):
                return
            # Deletion is best-effort at call sites (the sweep cron backstops),
            # but surface real storage failures to the caller's logger.
            raise

    async def list_archives(
        self, kind: StagingKind
    ) -> list[tuple[UUID, datetime | None]]:
        """(job_id, last_modified) for every staged archive of ``kind`` —
        the sweep cron's inventory. Malformed keys are skipped."""
        results: list[tuple[UUID, datetime | None]] = []
        async for batch in self.store.list_async(prefix=f"{kind}/"):
            for item in batch:
                if not isinstance(item, dict):
                    continue
                path = item.get("path") or ""
                parts = path.split("/")
                if len(parts) < 3:
                    continue
                try:
                    job_id = UUID(parts[1])
                except ValueError:
                    continue
                results.append((job_id, item.get("last_modified")))
        return results


def _is_missing_object_error(exc: Exception) -> bool:
    try:
        from obstore.exceptions import NotFoundError

        if isinstance(exc, NotFoundError):
            return True
    except ImportError:
        pass
    lowered = str(exc).lower()
    return "nosuchkey" in lowered or "not found" in lowered
