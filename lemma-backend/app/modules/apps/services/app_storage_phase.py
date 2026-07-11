"""Storage side of the app sagas: asset serving, archive download, bundle
upload, and deletion cleanup.

``AppStoragePhase`` is constructed with ONLY the app storage factory — it holds
**no app repository**, so it is impossible to issue a DB query here. Its methods
take plain dataclasses (resolved during the DB phase) and touch only object
storage, holding no pooled DB connection — safe to run after the resolving Unit
of Work has closed.

The DB→storage hand-off dataclasses live here (their natural home) so
``AppService`` and the storage phase can share them without an import cycle.
"""

from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

import structlog

from app.core.api.uploads import upload_source_sha256
from app.core.runtime_config import inject_runtime_config
from app.modules.apps.domain.entities import AppAssetDocument, AppReleaseEntity
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.domain.ports import AppStorageFactoryPort, AppStoragePort
from app.modules.apps.services.app_dist_bundle import load_app_dist_bundle

logger = structlog.get_logger()

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("image/svg+xml", ".svg")


@dataclass(frozen=True, slots=True)
class _AssetReadInputs:
    """Storage-read inputs resolved from the DB, carried out of the short UoW.

    Lets a controller resolve+authorize+ETag in a short UoW (connection released)
    and then read the asset bytes from storage with NO pooled connection held.
    """

    app_id: UUID
    pod_id: UUID
    dist_root_path: str
    normalized_asset_path: str
    quoted_etag: str
    app: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class _AppDeletionCleanup:
    """Storage paths to purge after an app row is deleted, carried out of the
    short UoW so the (potentially many-object) cleanup holds no connection."""

    app_id: UUID
    source_archive_path: str | None
    releases: tuple


@dataclass(frozen=True, slots=True)
class _UploadPlan:
    """DB-resolved plan for a bundle upload, carried across the storage write."""

    app_id: UUID
    pod_id: UUID
    name: str
    has_source: bool
    version: str | None
    release_root: str | None
    existing_release_id: UUID | None
    needs_dist_write: bool


@dataclass(frozen=True, slots=True)
class _WrittenBundle:
    source_path: str | None
    dist_archive_path: str | None


class AppStoragePhase:
    """Storage side of app sagas. No repository — DB-free by construction."""

    def __init__(self, file_manager_factory: AppStorageFactoryPort):
        self.file_manager_factory = file_manager_factory

    @staticmethod
    def _guess_media_type(path: str) -> str:
        media_type, _encoding = mimetypes.guess_type(path)
        return media_type or "application/octet-stream"

    async def read_asset(self, inputs: _AssetReadInputs) -> AppAssetDocument:
        """Read the asset bytes for resolved inputs. Holds NO DB connection."""
        storage = self.file_manager_factory(inputs.app_id)
        normalized_asset_path = inputs.normalized_asset_path
        is_entrypoint = normalized_asset_path in {"", "index.html"}
        requested_storage_path = (
            f"{inputs.dist_root_path}index.html"
            if not normalized_asset_path
            else f"{inputs.dist_root_path}{normalized_asset_path}"
        )
        try:
            content = await storage.read_file(requested_storage_path)
        except FileNotFoundError:
            # SPA fallback: paths without a file extension are client-side routes —
            # serve index.html so the React app can handle them.
            has_extension = (
                "." in PurePosixPath(normalized_asset_path).name
                if normalized_asset_path
                else False
            )
            if has_extension:
                raise AppNotFoundError(f"App asset '{normalized_asset_path}' not found")
            index_path = f"{inputs.dist_root_path}index.html"
            try:
                content = await storage.read_file(index_path)
            except FileNotFoundError as exc:
                raise AppNotFoundError("App index.html not found") from exc
            is_entrypoint = True
        if is_entrypoint:
            content = inject_runtime_config(
                content,
                inputs.pod_id,
                app=inputs.app,
            )
        return AppAssetDocument(
            content=content,
            media_type=self._guess_media_type(
                requested_storage_path if not is_entrypoint else "index.html"
            ),
            etag=inputs.quoted_etag,
            is_entrypoint=is_entrypoint,
        )

    async def read_archive(self, app_id: UUID, archive_path: str) -> bytes:
        """Read an archive's bytes from app storage for an already-resolved app.
        Storage only — no DB session."""
        storage = self.file_manager_factory(app_id)
        content = await storage.read_file(archive_path)
        if isinstance(content, str):
            return content.encode("utf-8")
        return content

    async def write_bundle(
        self,
        plan: _UploadPlan,
        source_archive_bytes: bytes | Path | None,
        dist_archive_bytes: bytes | Path | None,
    ) -> _WrittenBundle:
        """Write uploaded bytes to storage. Holds NO DB connection — call between
        resolve_upload_bundle and finalize_upload_bundle."""
        storage = self.file_manager_factory(plan.app_id)
        source_path: str | None = None
        dist_archive_path: str | None = None
        try:
            if plan.has_source and source_archive_bytes is not None:
                source_version = await asyncio.to_thread(
                    upload_source_sha256, source_archive_bytes
                )
                source_path = f"source/{source_version}/archive.zip"
                await storage.write_file(source_path, source_archive_bytes)
            if plan.needs_dist_write and dist_archive_bytes is not None:
                bundle = await asyncio.to_thread(
                    load_app_dist_bundle, dist_archive_bytes
                )
                for item in bundle.files:
                    await storage.write_file(
                        f"{plan.release_root}{item.path}", item.content
                    )
                dist_archive_path = f"{plan.release_root}archive.zip"
                await storage.write_file(dist_archive_path, dist_archive_bytes)
        except BaseException:
            await self._cleanup_written_paths(
                storage,
                plan,
                _WrittenBundle(
                    source_path=source_path,
                    dist_archive_path=dist_archive_path,
                ),
            )
            raise
        return _WrittenBundle(source_path=source_path, dist_archive_path=dist_archive_path)

    async def cleanup_written_bundle(
        self, plan: _UploadPlan, written: _WrittenBundle
    ) -> None:
        storage = self.file_manager_factory(plan.app_id)
        await self._cleanup_written_paths(storage, plan, written)

    async def _cleanup_written_paths(
        self,
        storage: AppStoragePort,
        plan: _UploadPlan,
        written: _WrittenBundle,
    ) -> None:
        if written.source_path:
            await self._delete_file_if_present(storage, written.source_path)
        if plan.needs_dist_write and plan.release_root:
            await storage.delete_prefix(plan.release_root)

    async def cleanup_storage(self, cleanup: _AppDeletionCleanup) -> None:
        """Delete an app's stored bytes. Holds NO DB connection; call after
        resolve_delete_app's UoW has committed. Best-effort (rows already gone)."""
        try:
            storage = self.file_manager_factory(cleanup.app_id)
            if cleanup.source_archive_path:
                await self._delete_file_if_present(storage, cleanup.source_archive_path)
            for release in cleanup.releases:
                await self._delete_release_files(storage, release)
            await storage.delete_prefix("")
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            logger.warning("App storage cleanup failed for %s: %s", cleanup.app_id, exc)

    async def _delete_release_files(
        self,
        storage: AppStoragePort,
        release: AppReleaseEntity,
    ) -> None:
        await storage.delete_prefix(release.dist_root_path)
        if release.dist_archive_path and not release.dist_archive_path.startswith(
            release.dist_root_path
        ):
            await self._delete_file_if_present(storage, release.dist_archive_path)

    @staticmethod
    async def _delete_file_if_present(storage: AppStoragePort, path: str) -> None:
        try:
            await storage.delete_file(path)
        except FileNotFoundError:
            return
