from __future__ import annotations

import asyncio
from typing import Callable, Optional
from uuid import UUID

from app.core.api.uploads import upload_source_sha256, upload_source_size
from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreFileNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileUpdateEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.domain.ports import (
    DatastoreSearchFactoryPort,
    DatastoreStoragePort,
)
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_file_storage_key,
    build_datastore_folder_storage_prefix,
)
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.projection import FileProjection
from app.modules.datastore.services.files.reader import FileReader
from app.modules.datastore.services.files.storage_phase import (
    FileStoragePhase,
    _PathDeletionCleanup,
    _StorageMove,
    _UpdatePlan,
)
from app.modules.datastore.services.files.transaction_writer import (
    FileTransactionWriter,
    _MARKDOWN_ASSET_NAMES_KEY,
    _MARKDOWN_SOURCE_KEY,
)
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider

logger = get_logger(__name__)


class FileWriter(FileTransactionWriter):
    """Write API: create file/folder, update (incl. move/rename), and delete
    paths. Owns the move/rename descendant-path rewrite."""

    def __init__(
        self,
        file_repository,
        storage: DatastoreStoragePort,
        search_factory_provider: Callable[[], DatastoreSearchFactoryPort],
        system_skill_files: SystemSkillFileProvider,
        authorizer: FileAuthorizer,
        path_resolver: PathResolver,
        projection: FileProjection,
        lookup: FileLookup,
        reader: FileReader,
    ):
        self.file_repository = file_repository
        self.storage = storage
        self._search_factory_provider = search_factory_provider
        self.system_skill_files = system_skill_files
        self.authorizer = authorizer
        self.paths = path_resolver
        self.projection = projection
        self.lookup = lookup
        self.reader = reader
        # Storage/search side of the update + delete sagas, on an object that
        # holds NO repository (DB-free by construction). The projection here is
        # built without a file_repository: the only methods used (storage_key,
        # delete_child_artifacts) are storage-only.
        self._storage_phase = FileStoragePhase(
            storage,
            search_factory_provider,
            FileProjection(storage, file_repository=None),
            path_resolver,
        )

    async def create_folder(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        description: Optional[str] = None,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        path = self.paths._resolve_api_path(
            path,
            requester_user_id=requester_user_id,
        )
        normalized_path = self.paths._normalize_path(path)
        if normalized_path == "/" or self.paths._is_personal_root_path(normalized_path):
            raise DatastoreValidationError("Root path already exists")
        self.system_skill_files.ensure_writable(normalized_path)

        parent_path, name = self.paths._split_parent_path(normalized_path)
        self.paths._ensure_personal_write_path(
            path=normalized_path,
            requester_user_id=requester_user_id,
        )
        parent_directory = await self._ensure_directory_path(
            pod_id,
            parent_path,
            requester_user_id=requester_user_id,
        )
        await self.authorizer.require_path_write_permission(
            requester_user_id=requester_user_id,
            pod_id=pod_id,
            path=normalized_path,
            resource_id=parent_directory.id if parent_directory is not None else None,
        )
        await self.lookup.ensure_path_available(
            pod_id=pod_id,
            path=normalized_path,
        )
        resolved_visibility = self.paths._resolve_visibility_for_path(
            normalized_path,
            requester_user_id,
            visibility,
        )

        folder = DatastoreFileEntity(
            pod_id=pod_id,
            owner_user_id=requester_user_id,
            kind=FileKind.FOLDER,
            visibility=resolved_visibility,
            path=normalized_path,
            name=name,
            description=description,
            mime_type="application/x-directory",
            size_bytes=0,
            search_enabled=False,
            status=FileStatus.NOT_REQUIRED,
        )
        return await self.file_repository.create(folder)

    async def _ensure_directory_path(
        self,
        pod_id: UUID,
        directory_path: str,
        *,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity | None:
        """Resolve ``directory_path`` to a folder, creating it and any missing
        ancestors on the way (``mkdir -p``).

        System roots stay synthetic: ``/`` and the personal ``/me`` root resolve
        to ``None`` (no backing row), and the read-only ``/skills`` overlay
        (root + built-in skill dirs) resolves to its synthetic entity. Only real,
        user-owned folders are materialized, with each level's visibility derived
        from its path (personal under ``/me``, pod-shared elsewhere) so an
        auto-created parent never widens access.
        """
        normalized_path = self.paths._normalize_path(directory_path)
        if normalized_path == "/" or self.paths._is_personal_root_path(normalized_path):
            return None

        if self.system_skill_files.is_path(normalized_path):
            synthetic = self.system_skill_files.get_entity(pod_id, normalized_path)
            if synthetic is not None:
                if not synthetic.is_folder:
                    raise DatastoreValidationError("Path must point to a folder")
                return synthetic
            # A non-built-in path under /skills (e.g. a user-authored skill dir)
            # has no overlay entity; fall through to materialize it as a real,
            # pod-visible folder.

        # Concurrent uploads commonly share a new directory. Hold a transaction-
        # scoped lock across the check/create decision so all losers re-read the
        # winner instead of surfacing a unique-constraint 500.
        await self.file_repository.acquire_path_lock(pod_id, normalized_path)
        existing = await self.file_repository.get_by_path(
            pod_id=pod_id,
            path=normalized_path,
        )
        if existing is not None:
            if not existing.is_folder:
                raise DatastoreValidationError("Path must point to a folder")
            if requester_user_id is not None:
                await self.authorizer.ensure_file_path_access(
                    existing,
                    requester_user_id,
                    ctx=ctx,
                )
            return existing

        parent_path, name = self.paths._split_parent_path(normalized_path)
        parent_directory = await self._ensure_directory_path(
            pod_id,
            parent_path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        self.system_skill_files.ensure_writable(normalized_path)
        if requester_user_id is not None:
            self.paths._ensure_personal_write_path(
                path=normalized_path,
                requester_user_id=requester_user_id,
            )
            await self.authorizer.require_path_write_permission(
                requester_user_id=requester_user_id,
                pod_id=pod_id,
                path=normalized_path,
                resource_id=parent_directory.id
                if parent_directory is not None
                else None,
                ctx=ctx,
            )
        resolved_visibility = self.paths._resolve_visibility_for_path(
            normalized_path,
            requester_user_id,
            None,
        )
        folder = DatastoreFileEntity(
            pod_id=pod_id,
            owner_user_id=requester_user_id,
            kind=FileKind.FOLDER,
            visibility=resolved_visibility,
            path=normalized_path,
            name=name,
            description=None,
            mime_type="application/x-directory",
            size_bytes=0,
            search_enabled=False,
            status=FileStatus.NOT_REQUIRED,
        )
        return await self.file_repository.create(folder)

    async def resolve_update_file(
        self,
        pod_id: UUID,
        update_entity: DatastoreFileUpdateEntity,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> _UpdatePlan:
        """Resolve + authorize + apply in-memory mutations (incl. rename
        collision checks) — DB only. The byte upload/move and search sync happen
        outside this UoW via write_update_storage / persist / finalize."""
        self._normalize_update_paths(update_entity, requester_user_id)

        file_entity = await self.reader.get_file_by_path(
            pod_id,
            update_entity.path,
            requester_user_id,
            ctx=ctx,
        )
        await self.authorizer.require_file_write_permission(
            file_entity=file_entity,
            requester_user_id=requester_user_id,
            message="Only pod editors and admins can update shared pod files",
            ctx=ctx,
        )

        previous_search_enabled = file_entity.search_enabled
        previous_path = file_entity.path
        previous_storage_key = (
            self.projection.storage_key(file_entity) if file_entity.is_file else None
        )
        descendants = await self._move_descendants(
            file_entity, previous_path, update_entity.new_path
        )

        self._apply_update_fields(file_entity, update_entity, requester_user_id)

        if update_entity.new_path is not None:
            await self._apply_new_path(
                file_entity,
                pod_id=pod_id,
                new_path=update_entity.new_path,
                requester_user_id=requester_user_id,
                ctx=ctx,
            )

        has_content = await self._apply_content_update(file_entity, update_entity)

        new_storage_key = (
            self.projection.storage_key(file_entity) if file_entity.is_file else None
        )
        storage_moves = self._storage_moves(
            file_entity=file_entity,
            descendants=descendants,
            previous_path=previous_path,
            previous_storage_key=previous_storage_key,
            new_storage_key=new_storage_key,
            has_content=has_content,
        )
        rename_moved = not has_content and bool(storage_moves)
        should_sync = self._update_requires_sync(
            update_entity=update_entity,
            file_entity=file_entity,
            previous_path=previous_path,
            previous_search_enabled=previous_search_enabled,
            has_content=has_content,
            rename_moved=rename_moved,
        )

        return _UpdatePlan(
            file_entity=file_entity,
            previous_path=previous_path,
            previous_search_enabled=previous_search_enabled,
            previous_storage_key=previous_storage_key,
            new_storage_key=new_storage_key,
            has_content=has_content,
            rename_moved=rename_moved,
            storage_moves=tuple(storage_moves),
            should_sync=should_sync,
            requester_user_id=requester_user_id,
        )

    def _normalize_update_paths(
        self,
        update_entity: DatastoreFileUpdateEntity,
        requester_user_id: UUID,
    ) -> None:
        if not update_entity.path:
            raise DatastoreValidationError("Path is required")
        update_entity.path = self.paths._resolve_api_path(
            update_entity.path, requester_user_id=requester_user_id
        )
        self.system_skill_files.ensure_writable(update_entity.path)
        if update_entity.new_path is not None:
            update_entity.new_path = self.paths._resolve_api_path(
                update_entity.new_path, requester_user_id=requester_user_id
            )
            self.system_skill_files.ensure_writable(update_entity.new_path)

    def _apply_update_fields(
        self,
        file_entity: DatastoreFileEntity,
        update_entity: DatastoreFileUpdateEntity,
        requester_user_id: UUID,
    ) -> None:
        if update_entity.description is not None:
            file_entity.update_description(update_entity.description)
        if update_entity.metadata is not None:
            file_entity.update_metadata(update_entity.metadata)
        if update_entity.content is not None and file_entity.metadata:
            cleared = dict(file_entity.metadata)
            had_source = cleared.pop(_MARKDOWN_SOURCE_KEY, None) is not None
            had_assets = cleared.pop(_MARKDOWN_ASSET_NAMES_KEY, None) is not None
            if had_source or had_assets:
                file_entity.update_metadata(cleared)
        if update_entity.search_enabled is not None:
            file_entity.set_search_enabled(update_entity.search_enabled)
        if update_entity.visibility is not None:
            file_entity.visibility = self.paths._resolve_visibility_for_path(
                file_entity.path, requester_user_id, update_entity.visibility
            )

    async def _apply_content_update(
        self,
        file_entity: DatastoreFileEntity,
        update_entity: DatastoreFileUpdateEntity,
    ) -> bool:
        content = update_entity.content
        if content is None:
            return False
        file_entity.size_bytes = upload_source_size(content)
        file_entity.content_sha256 = await asyncio.to_thread(
            upload_source_sha256, content
        )
        return True

    @staticmethod
    def _update_requires_sync(
        *,
        update_entity: DatastoreFileUpdateEntity,
        file_entity: DatastoreFileEntity,
        previous_path: str,
        previous_search_enabled: bool,
        has_content: bool,
        rename_moved: bool,
    ) -> bool:
        search_changed = (
            update_entity.search_enabled is not None
            and update_entity.search_enabled != previous_search_enabled
        )
        return (
            previous_path != file_entity.path
            or has_content
            or rename_moved
            or search_changed
        )

    async def _move_descendants(
        self,
        file_entity: DatastoreFileEntity,
        previous_path: str,
        new_path: str | None,
    ) -> list[DatastoreFileEntity]:
        if not file_entity.is_folder or new_path is None:
            return []
        return list(
            await self.file_repository.get_descendants(
                file_entity.pod_id, previous_path
            )
        )

    def _storage_moves(
        self,
        *,
        file_entity: DatastoreFileEntity,
        descendants: list[DatastoreFileEntity],
        previous_path: str,
        previous_storage_key: str | None,
        new_storage_key: str | None,
        has_content: bool,
    ) -> list[_StorageMove]:
        moves: list[_StorageMove] = []
        if not has_content and previous_storage_key and new_storage_key:
            if previous_storage_key != new_storage_key:
                moves.append(_StorageMove(previous_storage_key, new_storage_key))
        if previous_path == file_entity.path or not file_entity.is_folder:
            return moves
        for descendant in descendants:
            if not descendant.is_file:
                continue
            suffix = descendant.path.removeprefix(previous_path)
            destination_path = f"{file_entity.path}{suffix}"
            moves.append(
                _StorageMove(
                    self.projection.storage_key(descendant),
                    build_datastore_file_storage_key(
                        descendant.pod_id, destination_path
                    ),
                )
            )
        return moves

    async def write_update_storage(
        self, plan: _UpdatePlan, update_entity: DatastoreFileUpdateEntity
    ) -> None:
        """Storage phase of an update — delegated to the repo-free
        ``FileStoragePhase`` so it provably holds no DB connection."""
        await self._storage_phase.write_update(plan, update_entity)

    async def persist_update_file(self, plan: _UpdatePlan) -> DatastoreFileEntity:
        """Persist the mutated row (+ folder descendant paths) — DB only."""
        file_entity = plan.file_entity
        if plan.should_sync and self.paths._should_sync_projections(
            True,
            file_entity,
            previous_search_enabled=plan.previous_search_enabled,
        ):
            file_entity.mark_content_updated(plan.requester_user_id)

        updated_entity = await self.file_repository.update(file_entity)
        if plan.previous_path != updated_entity.path and updated_entity.is_folder:
            await self._update_descendant_paths(
                updated_entity, plan.previous_path, plan.requester_user_id
            )
        return updated_entity

    async def cleanup_uncommitted_update(self, plan: _UpdatePlan) -> None:
        await self._storage_phase.cleanup_uncommitted_update(plan)

    async def finalize_update_file(
        self, plan: _UpdatePlan, updated_entity: DatastoreFileEntity
    ) -> None:
        """Storage + search-index sync after the row is persisted — delegated to
        the repo-free ``FileStoragePhase`` (holds no DB connection)."""
        await self._storage_phase.finalize_update(plan, updated_entity)

    async def resolve_delete_path(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> _PathDeletionCleanup:
        """Authorize + delete the file/folder rows (DB only) and return the
        storage/search cleanup payload, so the (potentially many-object) storage
        purge + search-index removal run with no pooled connection held."""
        path = self.paths._resolve_api_path(
            path,
            requester_user_id=requester_user_id,
        )
        self.system_skill_files.ensure_writable(path)
        file_entity = await self.reader.get_file_by_path(
            pod_id,
            path,
            requester_user_id,
            ctx=ctx,
        )
        await self.authorizer.require_file_delete_permission(
            file_entity=file_entity,
            requester_user_id=requester_user_id,
            message="Only pod admins can delete shared pod files and folders",
            ctx=ctx,
        )
        descendants = []
        if file_entity.is_folder:
            descendants = list(
                await self.file_repository.get_descendants(
                    file_entity.pod_id,
                    file_entity.path,
                )
            )

        is_folder = file_entity.is_folder
        folder_prefix = (
            build_datastore_folder_storage_prefix(file_entity.pod_id, file_entity.path)
            if is_folder
            else None
        )
        files: list[dict[str, str]] = []
        for entity in sorted(
            [*descendants, file_entity],
            key=lambda item: (item.path.count("/"), item.path),
            reverse=True,
        ):
            if entity.is_file:
                files.append(
                    {
                        "file_id": str(entity.id),
                        "path": entity.path,
                        "storage_key": self.projection.storage_key(entity),
                    }
                )
            entity.mark_deleted(requester_user_id)
            deleted = await self.file_repository.delete_entity(entity)
            if not deleted:
                raise DatastoreFileNotFoundError(f"File {entity.path} not found")
        return _PathDeletionCleanup(
            pod_id=file_entity.pod_id,
            is_folder=is_folder,
            folder_prefix=folder_prefix,
            files=tuple(files),
        )

    async def cleanup_deleted_paths(
        self,
        pod_id: UUID,
        *,
        is_folder: bool,
        folder_prefix: str | None,
        files: list[dict[str, str]],
    ) -> None:
        """Purge storage bytes + search-index entries for already-deleted rows —
        delegated to the repo-free ``FileStoragePhase`` (holds no DB connection;
        search uses its own pool). Call after resolve_delete_path's UoW closed."""
        await self._storage_phase.cleanup_deleted_paths(
            pod_id,
            is_folder=is_folder,
            folder_prefix=folder_prefix,
            files=files,
        )

    async def _apply_new_path(
        self,
        file_entity: DatastoreFileEntity,
        *,
        pod_id: UUID,
        new_path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        normalized_path = self.paths._normalize_path(new_path)
        if normalized_path == file_entity.path:
            return
        if file_entity.is_folder and normalized_path.startswith(f"{file_entity.path}/"):
            raise DatastoreValidationError(
                "Folder cannot be moved into its own subtree"
            )

        parent_path, new_name = self.paths._split_parent_path(normalized_path)
        self.paths._ensure_personal_write_path(
            path=normalized_path,
            requester_user_id=requester_user_id,
        )
        await self._ensure_directory_path(
            pod_id,
            parent_path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        await self.lookup.ensure_path_available(
            pod_id=pod_id,
            path=normalized_path,
            exclude_file_id=file_entity.id,
        )
        file_entity.rename(new_name)
        file_entity.path = normalized_path
        # A rename can change the file's extension and therefore its type, which
        # flips indexability. Re-derive the MIME type so the subsequent
        # ``mark_content_updated`` (path change forces should_sync=True) and the
        # unsearchable-cleanup branch evaluate against the new type.
        if file_entity.is_file:
            file_entity.mime_type = self.paths._get_content_type(new_name)

    async def _update_descendant_paths(
        self,
        folder_entity: DatastoreFileEntity,
        previous_path: str,
        requester_user_id: UUID,
    ) -> None:
        descendants = await self.file_repository.get_descendants(
            folder_entity.pod_id,
            previous_path,
        )
        for descendant in descendants:
            suffix = descendant.path.removeprefix(previous_path)
            descendant.path = f"{folder_entity.path}{suffix}"
            if descendant.is_file and self.paths._should_sync_projections(
                True, descendant
            ):
                descendant.mark_content_updated(requester_user_id)
            await self.file_repository.update(descendant)
