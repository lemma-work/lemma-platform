"""Storage + search-index side of the file write/delete sagas.

``FileStoragePhase`` is constructed with ONLY storage/search collaborators — a
storage port, a search-service factory, a repo-free ``FileProjection``, and the
(pure) path resolver. It holds **no file repository**, so it is impossible to
issue a DB query from here: the methods touch only object storage and the search
index (which has its own pool). They take plain dataclasses (``_UpdatePlan`` /
``_PathDeletionCleanup``) carrying everything resolved during the DB phase, so
they are safe to run *after* the resolving Unit of Work has closed, holding no
pooled DB connection.

The DB→storage hand-off dataclasses live here (their natural home) so the writer
and the storage phase can share them without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreDomainError,
    DatastoreInfrastructureError,
    DatastoreObjectNotFoundError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileUpdateEntity,
)
from app.modules.datastore.domain.indexing_policy import is_indexable_mime_type
from app.modules.datastore.domain.ports import (
    DatastoreSearchFactoryPort,
    DatastoreStoragePort,
)
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.projection import FileProjection

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _PathDeletionCleanup:
    """Storage + search-index cleanup for already-deleted file/folder rows,
    carried out of the short UoW so the purge holds no pooled connection. The
    ``files`` entries (file rows only) are plain dicts so the payload is
    JSON-serializable for an offloaded worker task."""

    pod_id: UUID
    is_folder: bool
    folder_prefix: str | None
    files: tuple


@dataclass(frozen=True, slots=True)
class _StorageMove:
    source_key: str
    destination_key: str


@dataclass(frozen=True, slots=True)
class _UpdatePlan:
    """DB-resolved + in-memory-mutated state for a file update, carried across the
    storage write so the byte move/upload + search sync hold no connection."""

    file_entity: DatastoreFileEntity
    previous_path: str
    previous_search_enabled: bool
    previous_storage_key: str | None
    new_storage_key: str | None
    has_content: bool
    rename_moved: bool
    storage_moves: tuple[_StorageMove, ...]
    should_sync: bool
    requester_user_id: UUID


class FileStoragePhase:
    """Storage + search side of file sagas. No repository — DB-free by
    construction."""

    def __init__(
        self,
        storage: DatastoreStoragePort,
        search_factory_provider: Callable[[], DatastoreSearchFactoryPort],
        projection: FileProjection,
        path_resolver: PathResolver,
    ):
        self.storage = storage
        self._search_factory_provider = search_factory_provider
        self.projection = projection
        self.paths = path_resolver

    async def write_update(
        self, plan: _UpdatePlan, update_entity: DatastoreFileUpdateEntity
    ) -> None:
        """Upload new content / move the blob to its new key. Holds NO DB
        connection. The old blob is deleted later (finalize) — after the row is
        persisted — so a mid-flight failure can only orphan a blob, never lose
        data."""
        if plan.has_content:
            if plan.new_storage_key is None or update_entity.content is None:
                raise DatastoreInfrastructureError(
                    "Content update is missing its immutable storage target"
                )
            try:
                await self.storage.upload_file(
                    plan.new_storage_key, update_entity.content
                )
                stored_size = await self.storage.stat_file(plan.new_storage_key)
                if stored_size != plan.file_entity.size_bytes:
                    raise DatastoreInfrastructureError(
                        "Uploaded object size does not match staged content"
                    )
            except DatastoreDomainError as exc:
                raise DatastoreInfrastructureError(
                    "Failed to upload updated file content"
                ) from exc
        elif plan.storage_moves:
            copied: list[_StorageMove] = []
            try:
                for move in plan.storage_moves:
                    await self.storage.copy_file(move.source_key, move.destination_key)
                    copied.append(move)
            except DatastoreDomainError as exc:
                for move in reversed(copied):
                    try:
                        await self.storage.delete_file(move.destination_key)
                    except DatastoreDomainError:
                        logger.debug(
                            'datastore.storage_phase.rolling_back_staged_move_s.diagnostic',
                            exc_info=True,
                        )
                raise DatastoreInfrastructureError(
                    "Failed to move file content after rename"
                ) from exc

    async def finalize_update(
        self, plan: _UpdatePlan, updated_entity: DatastoreFileEntity
    ) -> None:
        """Storage + search-index sync after the row is persisted. Holds NO DB
        connection; best-effort throughout (orphans are swept, never torn rows)."""
        # Delete the old blob only now that the row points at the new key.
        if (
            plan.has_content
            and plan.previous_storage_key
            and plan.previous_storage_key != plan.new_storage_key
        ):
            try:
                await self.storage.delete_file(plan.previous_storage_key)
            except DatastoreDomainError:
                logger.debug(
                    'datastore.storage_phase.delete_superseded_original_s_s.diagnostic'
                )

        await self._delete_move_sources(plan.storage_moves)

        # Synchronous chunk + converted-artifact cleanup when a file is (or has
        # become) unsearchable — search disabled OR a non-indexable type (e.g.
        # after a rename changed its extension). This must NOT depend on the
        # reindex queue: the queue only enqueues PENDING + search_enabled files,
        # so a disabled/NOT_REQUIRED file is never processed and any previously
        # indexed chunks would otherwise be left stale. The removal is idempotent
        # (a no-op when there are no chunks), so it is also safe on a plain
        # non-indexable update. Keys use the file's CURRENT path (post-rename),
        # matching where storage/projection artifacts live after a move.
        search_service = self._search_factory_provider()(updated_entity.pod_id)
        if updated_entity.is_file and (
            not updated_entity.search_enabled
            or not is_indexable_mime_type(updated_entity.mime_type, updated_entity.name)
        ):
            try:
                await search_service.remove_file(updated_entity.id)
            except Exception:
                logger.debug(
                    'datastore.storage_phase.remove_indexed_chunks_unsearchable_file.diagnostic'
                )
            await self.projection.delete_child_artifacts(
                updated_entity.pod_id,
                updated_entity.path,
            )

        if plan.previous_path != updated_entity.path:
            if updated_entity.is_file and updated_entity.search_enabled:
                update_file_path = getattr(search_service, "update_file_path", None)
                if update_file_path is not None:
                    try:
                        await update_file_path(
                            updated_entity.id,
                            updated_entity.path,
                            self.paths._parent_path(updated_entity.path),
                        )
                    except Exception:
                        logger.debug(
                            'datastore.storage_phase.update_indexed_path_metadata_s.diagnostic'
                        )
            await self.projection.delete_child_artifacts(
                updated_entity.pod_id,
                plan.previous_path,
            )

    async def _delete_move_sources(
        self, storage_moves: tuple[_StorageMove, ...]
    ) -> None:
        for move in storage_moves:
            if move.source_key == move.destination_key:
                continue
            try:
                await self.storage.delete_file(move.source_key)
            except DatastoreDomainError:
                logger.debug(
                    'datastore.storage_phase.delete_moved_original_s_s.diagnostic'
                )

    async def cleanup_uncommitted_update(self, plan: _UpdatePlan) -> None:
        """Delete a newly written object when the following DB phase fails.

        The previous key is never touched, so the committed row remains readable.
        """
        if (
            plan.has_content
            and plan.new_storage_key
            and plan.new_storage_key != plan.previous_storage_key
        ):
            try:
                await self.storage.delete_file(plan.new_storage_key)
            except DatastoreObjectNotFoundError:
                return
            except Exception:
                logger.debug(
                    'datastore.storage_phase.clean_up_uncommitted_datastore_object.diagnostic'
                )
        for move in plan.storage_moves:
            if move.destination_key == move.source_key:
                continue
            try:
                await self.storage.delete_file(move.destination_key)
            except DatastoreObjectNotFoundError:
                continue
            except DatastoreDomainError:
                logger.debug(
                    'datastore.storage_phase.clean_up_staged_moved_object.diagnostic'
                )

    async def cleanup_deleted_paths(
        self,
        pod_id: UUID,
        *,
        is_folder: bool,
        folder_prefix: str | None,
        files: list[dict[str, str]],
    ) -> None:
        """Purge storage bytes + search-index entries for already-deleted rows.
        Holds NO main DB connection (search uses its own pool); call after
        resolve_delete_path's UoW has committed. Best-effort throughout."""
        search_service = self._search_factory_provider()(pod_id)
        if is_folder:
            if folder_prefix:
                try:
                    await self.storage.delete_prefix(folder_prefix)
                except DatastoreDomainError:
                    logger.debug(
                        'datastore.storage_phase.delete_folder_contents_storage_s.diagnostic'
                    )
            # The folder prefix removes canonical originals and colocated
            # derived children. Exact deletes remain idempotent and cover any
            # cleanup payload produced before a partial folder operation.
            for item in files:
                try:
                    await self.storage.delete_file(item["storage_key"])
                except Exception:
                    logger.debug('datastore.storage_phase.delete_file_s_s.diagnostic')
                try:
                    await search_service.remove_file(UUID(item["file_id"]))
                except Exception:
                    logger.debug(
                        'datastore.storage_phase.remove_indexed_chunks_s_s.diagnostic',
                        file_id=item["file_id"],
                    )
            return
        for item in files:
            try:
                await self.storage.delete_file(item["storage_key"])
            except Exception:
                logger.debug('datastore.storage_phase.delete_file_s_s.diagnostic')
            await self.projection.delete_child_artifacts(pod_id, item["path"])
            try:
                await search_service.remove_file(UUID(item["file_id"]))
            except Exception:
                logger.debug(
                    'datastore.storage_phase.remove_indexed_chunks_s_s.diagnostic',
                    file_id=item["file_id"],
                )
