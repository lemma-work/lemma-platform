from __future__ import annotations

from typing import Callable, Optional
from uuid import UUID

from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreInfrastructureError, DatastoreValidationError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileUpdateEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.domain.indexing_policy import is_indexable_mime_type
from app.modules.datastore.domain.ports import (
    DatastoreSearchFactoryPort,
    DatastoreStoragePort,
)
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_folder_storage_prefix,
)
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.projection import FileProjection
from app.modules.datastore.services.files.reader import FileReader
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider

logger = get_logger(__name__)


class FileWriter:
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

    async def create_file(
        self,
        pod_id: UUID,
        name: str,
        file_content: bytes,
        requester_user_id: UUID,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
        directory_path: str = "/",
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        directory_path = self.paths._resolve_api_path(
            directory_path,
            requester_user_id=requester_user_id,
        )
        self.paths._ensure_personal_write_path(
            path=directory_path,
            requester_user_id=requester_user_id,
        )
        directory = await self._ensure_directory_path(
            pod_id,
            directory_path,
            requester_user_id=requester_user_id,
        )
        await self.authorizer.require_path_write_permission(
            requester_user_id=requester_user_id,
            pod_id=pod_id,
            path=directory_path,
            resource_id=directory.id if directory is not None else None,
        )

        file_name = self.paths._normalize_name(name)
        path = self.paths._join_child_path(directory_path, file_name)
        self.system_skill_files.ensure_writable(path)
        await self.lookup.ensure_path_available(
            pod_id=pod_id,
            path=path,
        )
        resolved_visibility = self.paths._resolve_visibility_for_path(
            path,
            requester_user_id,
            visibility,
        )

        mime_type = self.paths._get_content_type(file_name)
        draft_status = (
            FileStatus.PENDING
            if (search_enabled and is_indexable_mime_type(mime_type, file_name))
            else FileStatus.NOT_REQUIRED
        )

        draft = DatastoreFileEntity(
            pod_id=pod_id,
            owner_user_id=requester_user_id,
            kind=FileKind.FILE,
            visibility=resolved_visibility,
            path=path,
            name=file_name,
            description=description,
            mime_type=mime_type,
            size_bytes=len(file_content),
            search_enabled=search_enabled,
            status=draft_status,
            metadata=metadata,
        )

        file_entity = await self.file_repository.create(draft)
        storage_path = self.projection.storage_key(file_entity)

        try:
            await self.storage.upload_file(storage_path, file_content)
        except Exception as exc:
            await self.file_repository.delete(file_entity.id)
            raise DatastoreInfrastructureError(
                "Failed to upload file content"
            ) from exc

        if self.paths._should_sync_projections(True, file_entity):
            file_entity.mark_created(requester_user_id)
        return await self.file_repository.update(file_entity)

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
                resource_id=parent_directory.id if parent_directory is not None else None,
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

    async def update_file_by_path(
        self,
        pod_id: UUID,
        update_entity: DatastoreFileUpdateEntity,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        if not update_entity.path:
            raise DatastoreValidationError("Path is required")
        update_entity.path = self.paths._resolve_api_path(
            update_entity.path,
            requester_user_id=requester_user_id,
        )
        self.system_skill_files.ensure_writable(update_entity.path)
        if update_entity.new_path is not None:
            update_entity.new_path = self.paths._resolve_api_path(
                update_entity.new_path,
                requester_user_id=requester_user_id,
            )
            self.system_skill_files.ensure_writable(update_entity.new_path)

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

        if update_entity.description is not None:
            file_entity.update_description(update_entity.description)
        if update_entity.metadata is not None:
            file_entity.update_metadata(update_entity.metadata)
        if update_entity.search_enabled is not None:
            file_entity.set_search_enabled(update_entity.search_enabled)
        if update_entity.visibility is not None:
            file_entity.visibility = self.paths._resolve_visibility_for_path(
                file_entity.path,
                requester_user_id,
                update_entity.visibility,
            )

        if update_entity.new_path is not None:
            await self._apply_new_path(
                file_entity,
                pod_id=pod_id,
                new_path=update_entity.new_path,
                requester_user_id=requester_user_id,
                ctx=ctx,
            )

        should_sync = previous_path != file_entity.path
        if update_entity.content is not None:
            try:
                await self.storage.upload_file(
                    self.projection.storage_key(file_entity), update_entity.content
                )
            except Exception as exc:
                raise DatastoreInfrastructureError(
                    "Failed to upload updated file content"
                ) from exc
            file_entity.size_bytes = len(update_entity.content)
            should_sync = True
        elif previous_storage_key and previous_storage_key != self.projection.storage_key(
            file_entity
        ):
            try:
                existing_content = await self.storage.download_file(
                    previous_storage_key
                )
                await self.storage.upload_file(
                    self.projection.storage_key(file_entity), existing_content
                )
                await self.storage.delete_file(previous_storage_key)
            except Exception as exc:
                raise DatastoreInfrastructureError(
                    "Failed to move file content after rename"
                ) from exc
            should_sync = True
        elif (
            update_entity.search_enabled is not None
            and update_entity.search_enabled != previous_search_enabled
        ):
            should_sync = True

        if should_sync and self.paths._should_sync_projections(
            True,
            file_entity,
            previous_search_enabled=previous_search_enabled,
        ):
            file_entity.mark_content_updated(requester_user_id)

        # Synchronous chunk + converted-artifact cleanup when a file is (or has
        # become) unsearchable — search disabled OR a non-indexable type (e.g.
        # after a rename changed its extension). This must NOT depend on the
        # reindex queue: the queue only enqueues PENDING + search_enabled files,
        # so a disabled/NOT_REQUIRED file is never processed and any previously
        # indexed chunks would otherwise be left stale. The removal is idempotent
        # (a no-op when there are no chunks), so it is also safe on a plain
        # non-indexable update. Keys use the file's CURRENT path (post-rename),
        # matching where storage/projection artifacts live after a move.
        if file_entity.is_file and (
            not file_entity.search_enabled
            or not is_indexable_mime_type(file_entity.mime_type, file_entity.name)
        ):
            search_service = self._search_factory_provider()(file_entity.pod_id)
            try:
                await search_service.remove_file(file_entity.id)
            except Exception as exc:
                logger.warning(
                    "Failed to remove indexed chunks for unsearchable file %s: %s",
                    file_entity.id,
                    exc,
                )
            await self.projection.delete_child_artifacts(
                file_entity.pod_id,
                file_entity.path,
            )

        updated_entity = await self.file_repository.update(file_entity)
        if previous_path != updated_entity.path and updated_entity.is_folder:
            await self._update_descendant_paths(
                updated_entity, previous_path, requester_user_id
            )
        if previous_path != updated_entity.path:
            if updated_entity.is_file and updated_entity.search_enabled:
                search_service = self._search_factory_provider()(updated_entity.pod_id)
                update_file_path = getattr(search_service, "update_file_path", None)
                if update_file_path is not None:
                    try:
                        await update_file_path(
                            updated_entity.id,
                            updated_entity.path,
                            self.paths._parent_path(updated_entity.path),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to update indexed path metadata for %s: %s",
                            updated_entity.id,
                            exc,
                        )
            await self.projection.delete_child_artifacts(
                updated_entity.pod_id,
                previous_path,
            )
        return updated_entity

    async def delete_file_by_path(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        await self.delete_path_by_path(
            pod_id=pod_id,
            path=path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )

    async def delete_path_by_path(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
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

        search_service = self._search_factory_provider()(file_entity.pod_id)
        if file_entity.is_folder:
            try:
                await self.storage.delete_prefix(
                    build_datastore_folder_storage_prefix(
                        file_entity.pod_id,
                        file_entity.path,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to delete folder contents from storage %s: %s",
                    file_entity.path,
                    exc,
                )
            # Child containers (converted markdown, figures, rendered pages) are
            # colocated under the folder prefix, so the delete above already
            # removed them — no separate derived-artifact sweep needed.

        for entity in sorted(
            [*descendants, file_entity],
            key=lambda item: (item.path.count("/"), item.path),
            reverse=True,
        ):
            await self.projection.delete_single_entity(
                entity,
                search_service=search_service,
                delete_storage=not file_entity.is_folder,
                actor_id=requester_user_id,
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
            if descendant.is_file and self.paths._should_sync_projections(True, descendant):
                descendant.mark_content_updated(requester_user_id)
            await self.file_repository.update(descendant)
