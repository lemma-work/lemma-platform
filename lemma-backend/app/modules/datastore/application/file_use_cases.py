"""Application/use-case layer for the datastore file sagas.

Each multi-phase file operation (update, delete, download, list-children,
download-child) has its HOME here: one method that owns the saga — the phase
sequencing, the short-UoW transaction boundaries, the data-integrity ordering
invariants, and the cross-resource cleanup dispatch. Controllers call exactly one
of these methods; the worker can call the same object.

A ``FileUseCases`` is built from a ``uow_factory`` (factory mode): it opens its
own SHORT units of work via ``pod_context_scope`` around each DB phase and does
the storage / search work outside them, so no pooled DB connection is ever held
across non-DB work. It is never handed a live UoW.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.scope import pod_context_scope
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileUpdateEntity,
)
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.infrastructure.reindex_queue import (
    enqueue_datastore_path_cleanup,
)
from app.modules.datastore.services.file_service import DatastoreFileService
from app.modules.datastore.services.files.http_cache import (
    if_none_match_matches,
    quote_content_etag,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FileDownload:
    """A resolved, authorized file plus its bytes (read after the UoW closed)."""

    entity: DatastoreFileEntity
    content: bytes | None
    not_modified: bool = False


@dataclass(frozen=True, slots=True)
class FileChildren:
    """A resolved document plus its derived child-artifact listing."""

    entity: DatastoreFileEntity
    children: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ChildArtifact:
    """A resolved document child artifact plus its rendered/read bytes."""

    entity: DatastoreFileEntity
    artifact_name: str
    content: bytes
    content_type: str


class FileUseCases:
    """Owns the datastore file sagas. Built from a uow_factory + a per-phase
    service builder; holds no DB connection across storage/search work."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[Any], DatastoreFileService],
    ):
        self._uow_factory = uow_factory
        self._build = service_builder

    async def create_file(
        self,
        *,
        pod_id: UUID,
        name: str,
        file_content: bytes | Path,
        request: Request,
        user_id: UUID,
        description: str | None = None,
        directory_path: str = "/",
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        """Persist draft -> upload bytes -> finalize, with no DB connection
        held while object storage receives the file."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            plan = await service.prepare_create_file(
                pod_id,
                name,
                file_content,
                scope.ctx,
                description=description,
                directory_path=directory_path,
                search_enabled=search_enabled,
                visibility=visibility,
            )

        storage_written = False
        try:
            await service.write_create_file(plan, file_content)
            storage_written = True
        finally:
            if not storage_written:
                async with pod_context_scope(
                    self._uow_factory,
                    request=request,
                    user_id=user_id,
                    pod_id=pod_id,
                ) as rollback_scope:
                    rollback_service = self._build(rollback_scope.uow)
                    await rollback_service.rollback_create_file(plan)
                await service.cleanup_create_storage(plan)

        try:
            async with pod_context_scope(
                self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
            ) as finalize_scope:
                finalize_service = self._build(finalize_scope.uow)
                return await finalize_service.finalize_create_file(plan)
        except SQLAlchemyError, DatastoreDomainError:
            try:
                async with pod_context_scope(
                    self._uow_factory,
                    request=request,
                    user_id=user_id,
                    pod_id=pod_id,
                ) as rollback_scope:
                    rollback_service = self._build(rollback_scope.uow)
                    await rollback_service.rollback_create_file(plan)
            finally:
                await service.cleanup_create_storage(plan)
            raise

    async def attach_user_markdown(
        self,
        *,
        pod_id: UUID,
        path: str,
        markdown_content: bytes | Path,
        images: list[tuple[str, bytes | Path]],
        request: Request,
        user_id: UUID,
    ) -> DatastoreFileEntity:
        """Authorize -> store assets -> persist metadata in short DB phases."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            plan = await service.prepare_user_markdown(pod_id, path, scope.ctx)

        asset_names = await service.write_user_markdown(
            plan,
            markdown_content,
            images=images,
        )

        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as finalize_scope:
            finalize_service = self._build(finalize_scope.uow)
            return await finalize_service.finalize_user_markdown(
                plan,
                asset_names=asset_names,
                ctx=finalize_scope.ctx,
            )

    async def update_file(
        self,
        *,
        pod_id: UUID,
        update_entity: DatastoreFileUpdateEntity,
        request: Request,
        user_id: UUID,
    ) -> DatastoreFileEntity:
        """Resolve+authorize+mutate (short UoW) -> move/upload bytes (no
        connection) -> persist the row (short UoW) -> sync storage+search (no
        connection). Upload-new precedes persist-row precedes delete-old, so a
        mid-flight failure can only orphan a blob, never lose data."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            plan = await service.resolve_update_file(
                pod_id, update_entity, ctx=scope.ctx
            )

        # Storage phase — no DB connection held (delegates to FileStoragePhase).
        await service.write_update_storage(plan, update_entity)

        try:
            async with pod_context_scope(
                self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
            ) as scope2:
                service = self._build(scope2.uow)
                updated = await service.persist_update_file(plan)
                entity = await service.get_file(updated.id, ctx=scope2.ctx)
        except SQLAlchemyError, DatastoreDomainError:
            await service.cleanup_uncommitted_update(plan)
            raise

        # Storage + search sync (delete-old blob last) — no DB connection held.
        await service.finalize_update_file(plan, updated)
        return entity

    async def delete_path(
        self,
        *,
        pod_id: UUID,
        path: str,
        request: Request,
        user_id: UUID,
    ) -> None:
        """Authorize + delete the rows (short UoW), then offload the storage +
        search-index purge to the worker so the API never holds a connection
        across the (potentially many-object) cleanup. If the enqueue is disabled
        or fails, clean up in-process (still no connection held) so deleted rows
        never leave orphaned blobs."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            cleanup = await service.resolve_delete_path(pod_id, path, ctx=scope.ctx)

        files = list(cleanup.files)
        enqueued = False
        if not settings.e2e_disable_worker_file_autoindex:
            # When the worker file-path is active, offload the purge; otherwise
            # (e2e without a datastore worker) fall through to in-process cleanup.
            try:
                enqueued = await enqueue_datastore_path_cleanup(
                    pod_id=cleanup.pod_id,
                    is_folder=cleanup.is_folder,
                    folder_prefix=cleanup.folder_prefix,
                    files=files,
                )
            except Exception:
                logger.debug(
                    'datastore.file_use_cases.enqueue_datastore_path_cleanup_s.diagnostic'
                )
                enqueued = False
        if not enqueued:
            await service.cleanup_deleted_paths(
                cleanup.pod_id,
                is_folder=cleanup.is_folder,
                folder_prefix=cleanup.folder_prefix,
                files=files,
            )

    async def download_file(
        self,
        *,
        pod_id: UUID,
        path: str,
        request: Request,
        user_id: UUID,
        if_none_match: str | None = None,
    ) -> FileDownload:
        """Resolve+authorize (short UoW), release the connection, then read the
        bytes from storage — so a slow/large download never pins a connection."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            entity = await service.resolve_readable_file(pod_id, path, ctx=scope.ctx)
        if if_none_match_matches(
            if_none_match, quote_content_etag(getattr(entity, "content_sha256", None))
        ):
            return FileDownload(entity=entity, content=None, not_modified=True)
        content = await service.read_file_content(entity)
        return FileDownload(entity=entity, content=content)

    async def list_children(
        self,
        *,
        pod_id: UUID,
        path: str,
        request: Request,
        user_id: UUID,
    ) -> FileChildren:
        """Resolve+authorize (short UoW), then build the child list from the
        storage manifest with no pooled connection held."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            entity = await service.resolve_children_file(pod_id, path, ctx=scope.ctx)
        children = await service.load_file_children(entity, user_id)
        return FileChildren(entity=entity, children=children)

    async def download_child(
        self,
        *,
        pod_id: UUID,
        path: str,
        request: Request,
        user_id: UUID,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> ChildArtifact:
        """Resolve+authorize the source file (short UoW), then render/read the
        child artifact from storage/CPU with no connection held."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            entity, artifact_rel = await service.resolve_child(
                pod_id, path, ctx=scope.ctx
            )
        artifact_name, content, content_type = await service.read_child_content(
            entity,
            artifact_rel,
            page_start=page_start,
            page_end=page_end,
        )
        return ChildArtifact(
            entity=entity,
            artifact_name=artifact_name,
            content=content,
            content_type=content_type,
        )
