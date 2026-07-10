"""Connection-safe create and Markdown write workflows for datastore files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.api.uploads import upload_source_has_content, upload_source_size
from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreFileNotFoundError,
    DatastoreInfrastructureError,
    DatastoreObjectNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.domain.indexing_policy import (
    is_indexable_mime_type,
    normalize_mime_type,
)
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_child_artifact_key,
    build_datastore_child_user_markdown_key,
)
from app.modules.datastore.services.files.write_plans import (
    CreateFilePlan,
    MarkdownAttachPlan,
)

logger = get_logger(__name__)

_ALREADY_TEXT_MIME_TYPES = frozenset(
    {"text/markdown", "text/x-markdown", "text/plain"}
)
_MARKDOWN_SOURCE_KEY = "markdown_source"
_USER_MARKDOWN_SOURCE = "user"
_MARKDOWN_ASSET_NAMES_KEY = "markdown_asset_names"


def _safe_asset_name(filename: str) -> str:
    base = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not base or base in (".", ".."):
        raise DatastoreValidationError(f"Invalid image filename: {filename!r}")
    return base


def _supports_user_markdown(file_entity: DatastoreFileEntity) -> bool:
    if not file_entity.is_file:
        return False
    if not is_indexable_mime_type(file_entity.mime_type, file_entity.name):
        return False
    base = normalize_mime_type(file_entity.mime_type, file_entity.name)
    return base not in _ALREADY_TEXT_MIME_TYPES


class FileTransactionWriter:
    """Create/Markdown state machines mixed into the lower-level writer."""

    file_repository: Any
    storage: Any
    authorizer: Any
    paths: Any
    projection: Any
    lookup: Any
    reader: Any
    system_skill_files: Any

    async def create_file(
        self,
        pod_id: UUID,
        name: str,
        file_content: bytes | Path,
        requester_user_id: UUID,
        description: str | None = None,
        metadata: dict | None = None,
        directory_path: str = "/",
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        plan = await self.prepare_create_file(
            pod_id,
            name,
            file_content,
            requester_user_id,
            description=description,
            metadata=metadata,
            directory_path=directory_path,
            search_enabled=search_enabled,
            visibility=visibility,
        )
        uploaded = False
        try:
            await self.write_create_file(plan, file_content)
            uploaded = True
        finally:
            if not uploaded:
                await self.rollback_create_file(plan)
        return await self.finalize_create_file(plan, reload=False)

    async def prepare_create_file(
        self,
        pod_id: UUID,
        name: str,
        file_content: bytes | Path,
        requester_user_id: UUID,
        description: str | None = None,
        metadata: dict | None = None,
        directory_path: str = "/",
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> CreateFilePlan:
        directory_path = self.paths._resolve_api_path(
            directory_path, requester_user_id=requester_user_id
        )
        self.paths._ensure_personal_write_path(
            path=directory_path, requester_user_id=requester_user_id
        )
        directory = await self._ensure_directory_path(
            pod_id, directory_path, requester_user_id=requester_user_id
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
        await self.lookup.ensure_path_available(pod_id=pod_id, path=path)
        resolved_visibility = self.paths._resolve_visibility_for_path(
            path, requester_user_id, visibility
        )
        mime_type = self.paths._get_content_type(file_name)
        draft_status = (
            FileStatus.PENDING
            if search_enabled and is_indexable_mime_type(mime_type, file_name)
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
            size_bytes=upload_source_size(file_content),
            search_enabled=search_enabled,
            status=draft_status,
            metadata=metadata,
        )
        entity = await self.file_repository.create(draft)
        return CreateFilePlan(
            entity=entity,
            storage_key=self.projection.storage_key(entity),
            requester_user_id=requester_user_id,
            emit_created_event=self.paths._should_sync_projections(True, entity),
        )

    async def write_create_file(
        self, plan: CreateFilePlan, file_content: bytes | Path
    ) -> None:
        try:
            await self.storage.upload_file(plan.storage_key, file_content)
        except Exception as exc:
            raise DatastoreInfrastructureError(
                "Failed to upload file content"
            ) from exc

    async def finalize_create_file(
        self, plan: CreateFilePlan, *, reload: bool = True
    ) -> DatastoreFileEntity:
        entity = await self.file_repository.get(plan.entity.id) if reload else plan.entity
        if entity is None:
            raise DatastoreFileNotFoundError("File draft no longer exists")
        if plan.emit_created_event:
            entity.mark_created(plan.requester_user_id)
        return await self.file_repository.update(entity)

    async def rollback_create_file(self, plan: CreateFilePlan) -> None:
        await self.file_repository.delete(plan.entity.id)

    async def cleanup_create_storage(self, plan: CreateFilePlan) -> None:
        try:
            await self.storage.delete_file(plan.storage_key)
        except DatastoreObjectNotFoundError:
            return

    async def attach_user_markdown(
        self,
        pod_id: UUID,
        path: str,
        markdown_content: bytes | Path,
        requester_user_id: UUID,
        images: list[tuple[str, bytes | Path]] | None = None,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        plan = await self.prepare_user_markdown(
            pod_id, path, requester_user_id, ctx=ctx
        )
        asset_names = await self.write_user_markdown(
            plan, markdown_content, images=images
        )
        return await self.finalize_user_markdown(
            plan, asset_names=asset_names, ctx=ctx, reload=False
        )

    async def prepare_user_markdown(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        *,
        ctx: Context | None = None,
    ) -> MarkdownAttachPlan:
        entity = await self.reader.get_file_by_path(
            pod_id, path, requester_user_id, ctx=ctx
        )
        await self.authorizer.require_file_write_permission(
            file_entity=entity,
            requester_user_id=requester_user_id,
            message="Only pod editors and admins can attach markdown to shared pod files",
            ctx=ctx,
        )
        if not _supports_user_markdown(entity):
            raise DatastoreValidationError(
                "Markdown can only be attached to an indexable, non-markdown "
                "document (e.g. a PDF, Word/ODT, HTML, RTF, or EPUB file)."
            )
        return MarkdownAttachPlan(entity=entity, requester_user_id=requester_user_id)

    async def write_user_markdown(
        self,
        plan: MarkdownAttachPlan,
        markdown_content: bytes | Path,
        *,
        images: list[tuple[str, bytes | Path]] | None = None,
    ) -> list[str]:
        if not await asyncio.to_thread(upload_source_has_content, markdown_content):
            raise DatastoreValidationError("Markdown content cannot be empty")
        entity = plan.entity
        await self.storage.upload_file(
            build_datastore_child_user_markdown_key(entity.pod_id, entity.path),
            markdown_content,
        )
        asset_names: list[str] = []
        for filename, content in images or []:
            name = _safe_asset_name(filename)
            await self.storage.upload_file(
                build_datastore_child_artifact_key(entity.pod_id, entity.path, name),
                content,
            )
            if name not in asset_names:
                asset_names.append(name)
        return asset_names

    async def finalize_user_markdown(
        self,
        plan: MarkdownAttachPlan,
        *,
        asset_names: list[str],
        ctx: Context | None = None,
        reload: bool = True,
    ) -> DatastoreFileEntity:
        entity = plan.entity
        if reload:
            entity = await self.reader.get_file(
                plan.entity.id, plan.requester_user_id, ctx=ctx
            )
        await self.authorizer.require_file_write_permission(
            file_entity=entity,
            requester_user_id=plan.requester_user_id,
            message="Only pod editors and admins can attach markdown to shared pod files",
            ctx=ctx,
        )
        metadata = dict(entity.metadata or {})
        metadata[_MARKDOWN_SOURCE_KEY] = _USER_MARKDOWN_SOURCE
        if asset_names:
            metadata[_MARKDOWN_ASSET_NAMES_KEY] = asset_names
        else:
            metadata.pop(_MARKDOWN_ASSET_NAMES_KEY, None)
        entity.update_metadata(metadata)
        entity.mark_content_updated(plan.requester_user_id)
        return await self.file_repository.update(entity)

    async def detach_user_markdown(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        entity = await self.reader.get_file_by_path(
            pod_id, path, requester_user_id, ctx=ctx
        )
        await self.authorizer.require_file_write_permission(
            file_entity=entity,
            requester_user_id=requester_user_id,
            message="Only pod editors and admins can detach markdown from shared pod files",
            ctx=ctx,
        )
        try:
            await self.storage.delete_file(
                build_datastore_child_user_markdown_key(pod_id, entity.path)
            )
        except DatastoreObjectNotFoundError:
            pass
        except Exception as exc:
            logger.warning(
                "Failed to delete user markdown for %s: %s",
                entity.path,
                exc,
                exc_info=True,
            )
        metadata = dict(entity.metadata or {})
        had_flag = metadata.pop(_MARKDOWN_SOURCE_KEY, None) is not None
        metadata.pop(_MARKDOWN_ASSET_NAMES_KEY, None)
        if not had_flag:
            return entity
        entity.update_metadata(metadata)
        entity.mark_content_updated(requester_user_id)
        return await self.file_repository.update(entity)

    async def _ensure_directory_path(
        self,
        pod_id: UUID,
        directory_path: str,
        requester_user_id: UUID,
    ) -> DatastoreFileEntity | None:
        raise NotImplementedError
