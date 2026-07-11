"""Application/use-case layer for the app sagas.

Each multi-phase app operation (delete, bundle upload, asset serving, archive
download) has its HOME here: one method that owns the phase sequencing, the
short-UoW transaction boundaries, and the release of the connection before any
storage I/O. Controllers call exactly one method; the worker can call the same
object.

Built from a ``uow_factory`` (factory mode): each DB phase runs in its own SHORT
unit of work via ``pod_context_scope`` (authed) or ``uow_scope`` (public,
unauthenticated by slug), and the storage reads/writes happen outside them, so no
pooled DB connection is ever held across non-DB work.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from fastapi import Request

from app.core.authorization.scope import pod_context_scope, uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.apps.domain.entities import AppAssetDocument, AppEntity
from app.modules.apps.services.app_service import AppService
from app.modules.apps.services.archive_validation import inspect_app_archive


class AppUseCases:
    """Owns the app sagas. Built from a uow_factory + a per-phase service
    builder; holds no DB connection across storage work."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[Any], AppService],
    ):
        self._uow_factory = uow_factory
        self._build = service_builder

    async def delete_app(
        self, *, pod_id: UUID, app_name: str, request: Request, user_id: UUID
    ) -> None:
        """Delete the app row (short UoW), then purge the stored bytes with no
        pooled connection held (the cleanup can touch many objects)."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            cleanup = await service.resolve_delete_app(
                pod_id, app_name, user_id, ctx=scope.ctx
            )
        await service.cleanup_app_storage(cleanup)

    async def upload_bundle(
        self,
        *,
        pod_id: UUID,
        app_name: str,
        request: Request,
        user_id: UUID,
        source_archive_bytes: bytes | Path | None,
        dist_archive_bytes: bytes | Path | None,
    ) -> AppEntity:
        """Resolve+authorize+dedup (short UoW) -> write the bundle bytes (no
        connection) -> persist the release pointer (short UoW)."""
        if source_archive_bytes is not None:
            await asyncio.to_thread(
                inspect_app_archive,
                source_archive_bytes,
                label="Source archive",
            )
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            plan = await service.resolve_upload_bundle(
                pod_id,
                app_name,
                user_id,
                has_source=source_archive_bytes is not None,
                dist_archive_bytes=dist_archive_bytes,
                ctx=scope.ctx,
            )
        written = await service.write_bundle_storage(
            plan, source_archive_bytes, dist_archive_bytes
        )
        try:
            async with pod_context_scope(
                self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
            ) as scope2:
                service = self._build(scope2.uow)
                return await service.finalize_upload_bundle(plan, written, user_id)
        except BaseException:
            await service.cleanup_written_bundle(plan, written)
            raise

    async def serve_asset(
        self,
        *,
        pod_id: UUID,
        app_name: str,
        request: Request,
        user_id: UUID,
        asset_path: str | None,
        request_etag: str | None,
    ) -> AppAssetDocument:
        """Resolve+authorize+ETag (short UoW), release the connection, then read
        the asset bytes from storage (unless a 304 short-circuits the read)."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            resolved = await service.resolve_app_asset(
                pod_id,
                app_name,
                user_id,
                asset_path=asset_path,
                request_etag=request_etag,
                ctx=scope.ctx,
            )
        if isinstance(resolved, AppAssetDocument):
            return resolved
        return await service.read_app_asset(resolved)

    async def serve_public_asset(
        self, *, slug: str, asset_path: str | None, request_etag: str | None
    ) -> AppAssetDocument:
        """Resolve by public slug (short UoW, unauthenticated), release the
        connection, then read the asset bytes from storage. Highest-traffic path
        (every app page load + static asset)."""
        async with uow_scope(self._uow_factory) as uow:
            service = self._build(uow)
            resolved = await service.resolve_app_asset_by_public_slug(
                slug, asset_path=asset_path, request_etag=request_etag
            )
        if isinstance(resolved, AppAssetDocument):
            return resolved
        return await service.read_app_asset(resolved)

    async def download_source_archive(
        self, *, pod_id: UUID, app_name: str, request: Request, user_id: UUID
    ) -> bytes:
        """Resolve+authorize the source archive location (short UoW), then read
        it from storage and return the bytes with no connection held."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            app_id, archive_path = await service.resolve_source_archive(
                pod_id, app_name, user_id, ctx=scope.ctx
            )
        return await service.read_archive(app_id, archive_path)

    async def download_dist_archive(
        self, *, pod_id: UUID, app_name: str, request: Request, user_id: UUID
    ) -> bytes:
        """Resolve+authorize the dist archive location (short UoW), then read it
        from storage and return the bytes with no connection held."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build(scope.uow)
            app_id, archive_path = await service.resolve_dist_archive(
                pod_id, app_name, user_id, ctx=scope.ctx
            )
        return await service.read_archive(app_id, archive_path)
