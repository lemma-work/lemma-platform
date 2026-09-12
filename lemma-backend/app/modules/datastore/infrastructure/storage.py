"""Datastore file storage adapters."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import obstore as obs
from obstore.exceptions import BaseError as ObstoreError
from app.core.config import settings
from app.core.object_storage import build_object_store, local_object_storage_path
from app.modules.datastore.domain.errors import (
    DatastoreInfrastructureError,
    DatastoreObjectNotFoundError,
)
from obstore.store import ObjectStore
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ObstoreDatastoreStorage:
    def __init__(self, store: ObjectStore):
        self.store = store

    async def upload_file(
        self, destination_blob_name: str, file_content: bytes | Path
    ) -> bool:
        await obs.put_async(
            self.store,
            destination_blob_name,
            file_content,
            use_multipart=isinstance(file_content, Path),
        )
        return True

    async def download_file(self, source_blob_name: str) -> bytes:
        try:
            response = await obs.get_async(self.store, source_blob_name)
            data = await response.bytes_async()
            return data.to_bytes()
        except (ObstoreError, FileNotFoundError) as exc:
            # A blob the metadata still points at can be absent (deleted out of
            # band, never written). Surface that as a typed not-found so callers
            # can return a clean 404 rather than leaking a storage 500.
            if self._is_missing_object_error(exc):
                raise DatastoreObjectNotFoundError(
                    f"Storage object not found: {source_blob_name}"
                ) from exc
            raise

    async def stat_file(self, source_blob_name: str) -> int:
        """Return an object's stored byte length, raising the typed not-found
        error used by downloads when metadata points at a missing object."""
        try:
            metadata = await obs.head_async(self.store, source_blob_name)
            return int(metadata["size"])
        except (ObstoreError, FileNotFoundError) as exc:
            if self._is_missing_object_error(exc):
                raise DatastoreObjectNotFoundError(
                    f"Storage object not found: {source_blob_name}"
                ) from exc
            raise DatastoreInfrastructureError("Failed to copy file") from exc

    async def copy_file(
        self, source_blob_name: str, destination_blob_name: str
    ) -> bool:
        """Copy an object without routing its bytes through the application."""
        try:
            await obs.copy_async(self.store, source_blob_name, destination_blob_name)
            return True
        except (ObstoreError, FileNotFoundError) as exc:
            if self._is_missing_object_error(exc):
                raise DatastoreObjectNotFoundError(
                    f"Storage object not found: {source_blob_name}"
                ) from exc
            raise

    async def iter_download(self, source_blob_name: str) -> AsyncIterator[bytes]:
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

    async def open_download(
        self,
        source_blob_name: str,
        *,
        byte_range: tuple[int, int] | None = None,
    ) -> tuple[int, AsyncIterator[bytes]]:
        """Open an object for streaming. Returns ``(total size, chunks)``.

        ``byte_range`` is half-open ``[start, end)``. The size returned is the
        **whole** object's, not the range's — that is what ``Content-Range``
        reports and what a client needs to know how much more there is.

        Distinct from ``iter_download`` because opening the object is what tells
        us whether it exists and how big it is. A caller that must answer 404 or
        set ``Content-Length`` *before* the first byte of a 200 goes out needs
        both facts up front, which an iterator alone cannot give it.
        """
        options = {"range": byte_range} if byte_range is not None else None
        try:
            response = await obs.get_async(
                self.store, source_blob_name, options=options
            )
        except Exception as exc:
            if self._is_missing_object_error(exc):
                raise DatastoreObjectNotFoundError(
                    f"Storage object not found: {source_blob_name}"
                ) from exc
            raise

        size = int(response.meta["size"])

        async def _chunks() -> AsyncIterator[bytes]:
            async for chunk in response.stream():
                yield bytes(chunk)

        return size, _chunks()

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
                return False
            logger.debug(
                "datastore.storage.deleting_datastore_file_s.propagated", exc_info=True
            )
            raise DatastoreInfrastructureError("Failed to delete file")

    async def move_prefix(self, source_prefix: str, destination_prefix: str) -> int:
        """Relocate every object under one prefix, and return how many moved.

        Copy-then-delete rather than a rename, because object stores have no
        rename: the two are the same operation and only the order matters. Copy
        first, so an interruption leaves both copies rather than neither.

        Defined once on the obstore base, so every backend gets it: the listing
        it needs is the same listing ``delete_prefix`` above already does, and
        the only reason a caller could not move a prefix before was that this
        method did not exist.
        """
        sources: list[str] = []
        try:
            async for batch in self.store.list_async(prefix=source_prefix):
                sources.extend(
                    item["path"]
                    for item in batch
                    if isinstance(item, dict) and item.get("path")
                )
            if not sources:
                return 0
            for source in sources:
                suffix = source[len(source_prefix) :].lstrip("/")
                await obs.copy_async(
                    self.store, source, f"{destination_prefix}{suffix}"
                )
            await obs.delete_async(self.store, sources)
            return len(sources)
        except Exception as exc:
            if self._is_missing_object_error(exc):
                return 0
            logger.debug(
                "datastore.storage.moving_datastore_prefix_s.propagated",
                exc_info=True,
            )
            raise DatastoreInfrastructureError("Failed to move folder contents")

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
                return 0
            logger.debug(
                "datastore.storage.deleting_datastore_prefix_s.propagated",
                exc_info=True,
            )
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
        root = (
            Path(root_path)
            if root_path is not None
            else local_object_storage_path("datastore")
        )
        super().__init__(
            build_object_store(
                local_prefix=root.expanduser(),
                force_backend="local",
            )
        )


class GCSDatastoreStorage(ObstoreDatastoreStorage):
    def __init__(self, bucket_name: str | None = None):
        bucket = bucket_name or settings.storage_bucket
        if not bucket:
            raise ValueError("GCS storage backend requires STORAGE_BUCKET")
        super().__init__(
            build_object_store(
                local_prefix=local_object_storage_path("datastore"),
                bucket_name=bucket,
                force_backend="gcs",
            )
        )


class S3DatastoreStorage(ObstoreDatastoreStorage):
    def __init__(self, bucket_name: str | None = None):
        bucket = bucket_name or settings.storage_bucket
        if not bucket:
            raise ValueError("S3 storage backend requires STORAGE_BUCKET")
        super().__init__(
            build_object_store(
                local_prefix=local_object_storage_path("datastore"),
                bucket_name=bucket,
                force_backend="s3",
            )
        )


class AzureDatastoreStorage(ObstoreDatastoreStorage):
    def __init__(self, container_name: str | None = None):
        container = container_name or settings.storage_bucket
        if not container:
            raise ValueError("Azure storage backend requires STORAGE_BUCKET")
        super().__init__(
            build_object_store(
                local_prefix=local_object_storage_path("datastore"),
                bucket_name=container,
                force_backend="azure",
            )
        )


def create_datastore_storage() -> ObstoreDatastoreStorage:
    backend = settings.effective_storage_backend()
    if backend == "gcs":
        return GCSDatastoreStorage()
    if backend == "s3":
        return S3DatastoreStorage()
    if backend == "azure":
        return AzureDatastoreStorage()
    return LocalDatastoreStorage()
