from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreFileNotFoundError
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_child_container_prefix,
    build_datastore_file_storage_key,
)

logger = get_logger(__name__)


def datastore_storage_key(file_entity: DatastoreFileEntity) -> str:
    """Storage object key for a file entity. Reusable outside the projection."""
    return build_datastore_file_storage_key(
        file_entity.pod_id,
        file_entity.path,
    )


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
        except Exception as exc:
            logger.warning(
                "Failed to delete derived child artifacts for %s: %s", path, exc, exc_info=True
            )

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
            except Exception as exc:
                logger.warning("Failed to delete file %s: %s", file_entity.path, exc, exc_info=True)
            await self.delete_child_artifacts(file_entity.pod_id, file_entity.path)

        if file_entity.is_file:
            try:
                await search_service.remove_file(file_entity.id)
            except Exception as exc:
                logger.warning(
                    "Failed to remove indexed chunks for %s: %s", file_entity.id, exc, exc_info=True
                )

        file_entity.mark_deleted(actor_id)
        deleted = await self.file_repository.delete_entity(file_entity)
        if not deleted:
            raise DatastoreFileNotFoundError(f"File {file_entity.path} not found")
