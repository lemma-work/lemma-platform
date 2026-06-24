"""Datastore file storage adapters."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import obstore as obs
from app.core.config import settings
from app.core.object_storage import local_object_storage_path
from app.modules.datastore.domain.errors import (
    DatastoreInfrastructureError,
    DatastoreObjectNotFoundError,
)
from obstore.store import GCSStore, LocalStore, ObjectStore
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ObstoreDatastoreStorage:
    def __init__(self, store: ObjectStore):
        self.store = store

    async def upload_file(
        self, destination_blob_name: str, file_content: bytes
    ) -> bool:
        await obs.put_async(self.store, destination_blob_name, file_content)
        return True

    async def download_file(self, source_blob_name: str) -> bytes:
        try:
            response = await obs.get_async(self.store, source_blob_name)
            data = await response.bytes_async()
            return data.to_bytes()
        except Exception as exc:
            # A blob the metadata still points at can be absent (deleted out of
            # band, never written). Surface that as a typed not-found so callers
            # can return a clean 404 rather than leaking a storage 500.
            if self._is_missing_object_error(exc):
                raise DatastoreObjectNotFoundError(
                    f"Storage object not found: {source_blob_name}"
                ) from exc
            raise

    async def iter_download(
        self, source_blob_name: str
    ) -> AsyncIterator[bytes]:
        """Stream an object as byte chunks without loading it fully into memory.

        Used for large originals (e.g. a PDF being shipped to Kreuzberg for page
        rendering) so peak memory is one chunk, not the whole file.
        """
        try:
            response = await obs.get_async(self.store, source_blob_name)
        except Exception as exc:
            if self._is_missing_object_error(exc):
                raise DatastoreObjectNotFoundError(
                    f"Storage object not found: {source_blob_name}"
                ) from exc
            raise
        async for chunk in response.stream():
            yield bytes(chunk)

    async def get_signed_url(self, blob_name: str, expires_hours: int = 1) -> str:
        return await obs.sign_async(
            self.store, "GET", blob_name, expires_in=timedelta(hours=expires_hours)
        )

    async def delete_file(self, blob_name: str) -> bool:
        try:
            await obs.delete_async(self.store, blob_name)
            return True
        except Exception as exc:
            if self._is_missing_object_error(exc):
                logger.info("Skipping delete for missing datastore object %s", blob_name)
                return False
            logger.error("Error deleting datastore file: %s", exc)
            raise DatastoreInfrastructureError("Failed to delete file")

    async def delete_prefix(self, prefix: str) -> int:
        deleted_paths: list[str] = []
        try:
            async for batch in self.store.list_async(prefix=prefix):
                deleted_paths.extend(
                    item["path"]
                    for item in batch
                    if isinstance(item, dict) and item.get("path")
                )
            if not deleted_paths:
                return 0
            await obs.delete_async(self.store, deleted_paths)
            return len(deleted_paths)
        except Exception as exc:
            if self._is_missing_object_error(exc):
                logger.info("Skipping delete for missing datastore prefix %s", prefix)
                return 0
            logger.error("Error deleting datastore prefix: %s", exc)
            raise DatastoreInfrastructureError("Failed to delete folder contents")

    def _is_missing_object_error(self, exc: Exception) -> bool:
        try:
            from obstore.exceptions import NotFoundError

            if isinstance(exc, NotFoundError):
                return True
        except ImportError:
            pass
        lowered = str(exc).lower()
        return "nosuchkey" in lowered or "not found" in lowered


class LocalDatastoreStorage(ObstoreDatastoreStorage):
    def __init__(self, root_path: str | Path | None = None):
        root = Path(root_path) if root_path is not None else local_object_storage_path(
            "datastore"
        )
        super().__init__(LocalStore(prefix=root.expanduser(), mkdir=True))


class GCSDatastoreStorage(ObstoreDatastoreStorage):
    def __init__(self, bucket_name: str | None = None):
        bucket = bucket_name or settings.gcs_storage_bucket
        if not bucket:
            raise ValueError("GCS storage backend requires GCS_STORAGE_BUCKET")
        super().__init__(GCSStore(bucket=bucket))


def create_datastore_storage() -> ObstoreDatastoreStorage:
    if settings.effective_storage_backend() == "gcs":
        return GCSDatastoreStorage()
    return LocalDatastoreStorage()
