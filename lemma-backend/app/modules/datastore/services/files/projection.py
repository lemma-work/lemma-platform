from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreFileNotFoundError,
    DatastoreObjectNotFoundError,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage_paths import (
    FileKeys,
    StoredFile,
    build_datastore_child_container_prefix,
    content_keys,
    fallback_keys,
    is_object_layout,
    object_file_prefix,
    readable_keys,
)

logger = get_logger(__name__)


def datastore_storage_key(file_entity: DatastoreFileEntity) -> str:
    """Where the file's current bytes are, in whichever layout it is stored."""
    return content_keys(file_entity).original


class FileProjection:
    """Owns the side artifacts of a file: its primary storage object, the hidden
    colocated child container (converted markdown, figures, rendered pages), and
    search-index chunks. Single home for derived-artifact cleanup, shared with
    the file-processing service and the writer."""

    def __init__(self, storage: DatastoreStoragePort, file_repository):
        self.storage = storage
        self.file_repository = file_repository

    def storage_key(self, file_entity: DatastoreFileEntity) -> str:
        return datastore_storage_key(file_entity)

    async def delete_child_artifacts(
        self,
        pod_id: UUID,
        path: str,
    ) -> None:
        """Drop the file's hidden child container — converted markdown, extracted
        figures, manifest, and cached rendered pages — in a single prefix delete
        (they're colocated under ``…/.{name}/``)."""
        try:
            await self.storage.delete_prefix(
                build_datastore_child_container_prefix(pod_id, path)
            )
        except Exception:
            logger.debug(
                "datastore.projection.delete_derived_child_artifacts_s.diagnostic",
                exc_info=True,
            )

    async def delete_all_revisions(self, file_entity: DatastoreFileEntity) -> None:
        """Every byte the file has, original and derived, in either layout."""
        if is_object_layout(file_entity) and file_entity.id is not None:
            try:
                await self.storage.delete_prefix(
                    object_file_prefix(file_entity.pod_id, file_entity.id)
                )
            except Exception:
                logger.debug(
                    "datastore.projection.delete_object_revisions.diagnostic",
                    exc_info=True,
                )
            return
        await self.delete_child_artifacts(file_entity.pod_id, file_entity.path)

    async def delete_single_entity(
        self,
        file_entity: DatastoreFileEntity,
        *,
        search_service,
        delete_storage: bool = True,
        actor_id: UUID | None = None,
    ) -> None:
        if file_entity.is_file and delete_storage:
            try:
                await self.storage.delete_file(self.storage_key(file_entity))
            except Exception:
                logger.debug(
                    "datastore.projection.delete_file_s_s.diagnostic", exc_info=True
                )
            await self.delete_all_revisions(file_entity)

        if file_entity.is_file:
            try:
                await search_service.remove_file(file_entity.id)
            except Exception:
                logger.debug(
                    "datastore.projection.remove_indexed_chunks_s_s.diagnostic",
                    exc_info=True,
                )

        file_entity.mark_deleted(actor_id)
        deleted = await self.file_repository.delete_entity(file_entity)
        if not deleted:
            raise DatastoreFileNotFoundError(f"File {file_entity.path} not found")


async def read_stored(
    storage: DatastoreStoragePort,
    file_entity: StoredFile,
    key_of: Callable[[FileKeys], str],
) -> bytes:
    """Read one of a file's objects, from the other layout if it is not here.

    While the background copy runs, a reader can hold a row that says one
    layout and find the bytes already moved to (or not yet copied from) the
    other. Looking in both is what makes the switch invisible to readers.
    """
    try:
        return await storage.download_file(key_of(readable_keys(file_entity)))
    except DatastoreObjectNotFoundError:
        other = fallback_keys(file_entity)
        if other is None:
            raise
        return await storage.download_file(key_of(other))
