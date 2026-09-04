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

from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable
from uuid import UUID

from fastapi import Request

from app.core.authorization.scope import pod_context_scope, uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.apps.domain.entities import AppAssetDocument, AppEntity
from app.modules.apps.services.app_service import AppService
from app.modules.apps.services.archive_validation import inspect_app_archive
from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger

logger = get_logger(__name__)


if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.apps.services.app_release_service import (
        AppReleaseService,
        ReleaseHistory,
    )


class AppUseCases:
    """Owns the app sagas. Built from a uow_factory + a per-phase service
    builder; holds no DB connection across storage work."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[SqlAlchemyUnitOfWork], AppService],
        release_service_builder: Callable[[SqlAlchemyUnitOfWork], AppReleaseService]
        | None = None,
    ):
        self._uow_factory = uow_factory
        self._build = service_builder
        self._release_builder = release_service_builder

    def _build_releases(self, uow: SqlAlchemyUnitOfWork) -> AppReleaseService:
        if self._release_builder is not None:
            return self._release_builder(uow)
        from app.modules.apps.services.app_release_service import AppReleaseService

        return AppReleaseService(self._build(uow).repository)

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
            await run_blocking(
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
                app = await service.finalize_upload_bundle(plan, written, user_id)
        except BaseException:
            await service.cleanup_written_bundle(plan, written)
            raise
        # A deploy is when this app's storage grows, so it is when surplus
        # releases are worth removing -- no scheduled job needed for the common
        # case. Best-effort by construction: the release is already live, and
        # failing the deploy because a prune failed would be absurd. The daily
        # sweep retries whatever this misses.
        await self._prune_releases_quietly(pod_id, plan.name, request, user_id)
        return app

    async def _prune_releases_quietly(
        self, pod_id: UUID, app_name: str, request: Request, user_id: UUID
    ) -> None:
        from app.modules.apps.config import apps_settings

        if not apps_settings.app_release_retention_enabled:
            return

        from app.modules.apps.services.app_release_retention import (
            PRUNE_FAILURES,
            AppReleaseRetention,
        )

        try:
            async with pod_context_scope(
                self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
            ) as scope:
                service = self._build(scope.uow)
                app = await service.get_app_by_name(
                    pod_id, app_name, user_id, ctx=scope.ctx
                )
                if app is None:
                    return
                retention = AppReleaseRetention(
                    service.repository, service.file_manager_factory
                )
                prune_plan = await retention.plan(app)
            # Storage deletes run outside the unit of work, holding no pooled
            # connection -- pruning can touch many objects.
            await retention.execute(prune_plan)
            async with self._uow_factory() as completed_uow:
                await self._build(completed_uow).repository.mark_releases_purged(
                    prune_plan.version_ids
                )
                await completed_uow.commit()
        except PRUNE_FAILURES:
            logger.warning(
                "apps.app_use_cases.release_retention.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )

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
        self,
        *,
        slug: str,
        asset_path: str | None,
        request_etag: str | None,
        release_ref: str | None = None,
    ) -> AppAssetDocument:
        """Resolve by public slug (short UoW, unauthenticated), release the
        connection, then read the asset bytes from storage. Highest-traffic path
        (every app page load + static asset).

        ``release_ref`` serves a preview host's specific release instead of the
        live one; it inherits the same PUBLIC-only rule, since a preview is the
        same shell with the same separately-authorized data calls.
        """
        async with uow_scope(self._uow_factory) as uow:
            service = self._build(uow)
            resolved = await service.resolve_app_asset_by_public_slug(
                slug,
                asset_path=asset_path,
                request_etag=request_etag,
                release_ref=release_ref,
            )
        if isinstance(resolved, AppAssetDocument):
            return resolved
        return await service.read_app_asset(resolved)

    async def list_releases(
        self, *, pod_id: UUID, app_name: str, request: Request, user_id: UUID
    ) -> ReleaseHistory:
        """List an app's release history (one short UoW, no storage)."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            return await self._build_releases(scope.uow).list_releases(
                pod_id, app_name, ctx=scope.ctx
            )

    async def promote_release(
        self,
        *,
        pod_id: UUID,
        app_name: str,
        release_ref: str,
        request: Request,
        user_id: UUID,
    ):
        """Make an existing release live. One short UoW -- promotion moves a
        pointer, so no bytes are read or written."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            return await self._build_releases(scope.uow).promote_release(
                pod_id, app_name, release_ref, ctx=scope.ctx
            )

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
