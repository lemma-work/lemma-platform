"""App service."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import structlog

from app.core.authorization.context import Context, ResourceRef, ResourceType, ResourceVisibility
from app.core.html_document import wrap_html_fragment
from app.core.ports.widget_content import WidgetArtifact
from app.core.runtime_config import inject_runtime_config, runtime_config_token
from app.core.authorization.permissions import Permissions
from app.core.helpers.slug import normalize_public_slug, normalize_resource_name
from app.modules.apps.domain.entities import (
    AppAssetDocument,
    AppEntity,
    AppReleaseEntity,
    AppStatus,
    AppUpdateEntity,
)
from app.modules.apps.domain.errors import (
    AppConflictError,
    AppNotFoundError,
    AppValidationError,
)
from app.modules.apps.domain.ports import (
    AppRepositoryPort,
    AppStorageFactoryPort,
    AppStoragePort,
)
from app.modules.apps.services.app_dist_bundle import load_app_dist_bundle
from app.modules.apps.services.app_html_validation import lint_app_html
from app.modules.pod.domain.pod_entities import PodRole
from app.modules.pod.domain.visibility import (
    PERSONAL_VISIBILITY_VALUES,
    POD_VISIBILITY_VALUES,
)

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


class AppService:
    def __init__(
        self,
        app_repository: AppRepositoryPort,
        file_manager_factory: AppStorageFactoryPort,
        authorization_service: object,
    ):
        self.repository = app_repository
        self.file_manager_factory = file_manager_factory
        self.authorization_service = authorization_service

    @staticmethod
    def _quote_etag(etag: str | None) -> str | None:
        if not etag:
            return None
        return f'"{etag}"'

    @classmethod
    def _etag_matches(cls, candidate: str | None, request_header: str | None) -> bool:
        if not candidate or not request_header:
            return False

        normalized_candidate = candidate.strip().strip('"')
        for raw_value in request_header.split(","):
            value = raw_value.strip()
            if value == "*":
                return True
            if value.startswith("W/"):
                value = value[2:]
            if value.strip().strip('"') == normalized_candidate:
                return True
        return False

    @staticmethod
    def _normalize_requested_asset_path(asset_path: str | None) -> str:
        normalized = (asset_path or "").replace("\\", "/").strip("/")
        if not normalized:
            return ""

        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise AppNotFoundError("App asset not found")
        return path.as_posix()

    @staticmethod
    def _guess_media_type(path: str) -> str:
        media_type, _encoding = mimetypes.guess_type(path)
        return media_type or "application/octet-stream"

    async def _validate_unique_public_slug(
        self,
        *,
        public_slug: str,
        current_app_id: UUID | None = None,
    ) -> None:
        existing = await self.repository.get_by_public_slug(public_slug)
        if existing and existing.id != current_app_id:
            raise AppConflictError(
                f"Public slug '{public_slug}' is already taken. App slugs are "
                "globally unique across all pods, so this one may belong to another "
                "pod and won't show up in your `apps list`. Choose a different slug."
            )

    async def _get_current_release(
        self,
        app: AppEntity,
        *,
        raise_not_found_name: str,
    ) -> AppReleaseEntity:
        if not app.current_release_id:
            raise AppNotFoundError(f"Build not found for app '{raise_not_found_name}'")

        release = await self.repository.get_release(app.current_release_id)
        if not release:
            raise AppNotFoundError(f"Current release not found for app '{raise_not_found_name}'")
        return release

    async def _resolve_asset_document(
        self,
        app: AppEntity,
        *,
        raise_not_found_name: str,
        asset_path: str | None,
        request_etag: str | None = None,
    ) -> _AssetReadInputs | AppAssetDocument:
        """DB phase: resolve release + ETag. Returns a not-modified document on a
        304 (no storage needed) or the inputs for the storage read otherwise."""
        release = await self._get_current_release(app, raise_not_found_name=raise_not_found_name)
        normalized_asset_path = self._normalize_requested_asset_path(asset_path)
        entrypoint_request = normalized_asset_path in {"", "index.html"}
        # Entrypoints carry the injected pod context, so a pod/api/auth change
        # must bust the cached HTML — fold the config hash into the ETag.
        etag = (
            f"{release.version}.{runtime_config_token(app.pod_id)}"
            if entrypoint_request
            else release.version
        )
        quoted_etag = self._quote_etag(etag)

        if self._etag_matches(etag, request_etag):
            return AppAssetDocument(
                etag=quoted_etag,
                not_modified=True,
                is_entrypoint=entrypoint_request,
            )

        return _AssetReadInputs(
            app_id=app.id,
            pod_id=app.pod_id,
            dist_root_path=release.dist_root_path,
            normalized_asset_path=normalized_asset_path,
            quoted_etag=quoted_etag,
        )

    async def read_app_asset(self, inputs: _AssetReadInputs) -> AppAssetDocument:
        """Storage phase: read the asset bytes. Holds NO DB connection — safe to
        call after the resolving UoW has closed."""
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
            has_extension = "." in PurePosixPath(normalized_asset_path).name if normalized_asset_path else False
            if has_extension:
                raise AppNotFoundError(f"App asset '{normalized_asset_path}' not found")
            index_path = f"{inputs.dist_root_path}index.html"
            try:
                content = await storage.read_file(index_path)
            except FileNotFoundError as exc:
                raise AppNotFoundError("App index.html not found") from exc
            is_entrypoint = True
        if is_entrypoint:
            content = inject_runtime_config(content, inputs.pod_id)
        return AppAssetDocument(
            content=content,
            media_type=self._guess_media_type(requested_storage_path if not is_entrypoint else "index.html"),
            etag=inputs.quoted_etag,
            is_entrypoint=is_entrypoint,
        )

    async def _build_asset_document(
        self,
        app: AppEntity,
        *,
        raise_not_found_name: str,
        asset_path: str | None,
        request_etag: str | None = None,
    ) -> AppAssetDocument:
        # Back-compat single-call path (holds the connection across the storage
        # read). Streaming/serving controllers should instead resolve in a short
        # UoW then call read_app_asset outside it.
        resolved = await self._resolve_asset_document(
            app,
            raise_not_found_name=raise_not_found_name,
            asset_path=asset_path,
            request_etag=request_etag,
        )
        if isinstance(resolved, AppAssetDocument):
            return resolved
        return await self.read_app_asset(resolved)

    async def _delete_release_files(
        self,
        storage: AppStoragePort,
        release: AppReleaseEntity,
    ) -> None:
        await storage.delete_prefix(release.dist_root_path)
        if release.dist_archive_path and not release.dist_archive_path.startswith(release.dist_root_path):
            await self._delete_file_if_present(storage, release.dist_archive_path)

    @staticmethod
    async def _delete_file_if_present(storage: AppStoragePort, path: str) -> None:
        try:
            await storage.delete_file(path)
        except FileNotFoundError:
            return

    async def _require_pod_permission(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        required_role: PodRole,
        message: str,
        resource_type: ResourceType = ResourceType.POD,
        resource_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        _ = message
        action = {
            PodRole.VIEWER: Permissions.APP_READ,
            PodRole.EDITOR: Permissions.APP_UPDATE,
            PodRole.ADMIN: Permissions.APP_DELETE,
        }[required_role]
        if ctx is None:
            raise RuntimeError("Context is required for app authorization")
        await ctx.require(
            action,
            ResourceRef(
                resource_type=resource_type,
                resource_id=resource_id or pod_id,
                pod_id=pod_id,
            ),
        )

    async def create_app(self, entity: AppEntity, user_id: UUID) -> AppEntity:
        return await self.create_app_with_context(entity, user_id, ctx=None)

    async def create_app_with_context(
        self,
        entity: AppEntity,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> AppEntity:
        if ctx is not None:
            await ctx.require(Permissions.APP_CREATE, ResourceRef.pod(entity.pod_id))
        else:
            await self._require_pod_permission(
                pod_id=entity.pod_id,
                user_id=user_id,
                required_role=PodRole.EDITOR,
                message=f"User {user_id} does not have editor access to pod {entity.pod_id}",
                resource_type=ResourceType.POD,
                resource_id=entity.pod_id,
                ctx=ctx,
            )

        existing = await self.repository.get_by_name(entity.pod_id, entity.name)
        if existing:
            raise AppConflictError(
                f"App with name '{entity.name}' already exists in pod {entity.pod_id}"
            )

        entity.public_slug = normalize_public_slug(entity.public_slug or entity.name)
        if not entity.public_slug:
            raise AppValidationError("public_slug cannot be empty")
        await self._validate_unique_public_slug(public_slug=entity.public_slug)

        entity.user_id = user_id
        self._normalize_app_visibility(entity)
        created = await self.repository.create(entity)
        if ctx is not None:
            refreshed = await self.repository.get_by_name(entity.pod_id, entity.name, ctx=ctx)
            return refreshed or created
        return created

    @staticmethod
    def _single_index_html_zip(html: str) -> bytes:
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("index.html", html)
        return buffer.getvalue()

    async def create_app_from_widget(
        self,
        pod_id: UUID,
        user_id: UUID,
        *,
        artifact: WidgetArtifact,
        name: str,
        public_slug: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
        ctx: Context | None = None,
    ) -> AppEntity:
        """Promote a resolved widget artifact into a persisted app.

        The widget and the app are the same artifact at two lifecycle stages:
        the stored HTML is wrapped as a standalone document (no embed bridge)
        and deployed as the app's bundle — identical to what the widget showed.
        """
        for issue in lint_app_html(artifact.content):
            logger.warning(
                "app_html_lint", app=name, pod_id=str(pod_id), issue=issue
            )
        document = wrap_html_fragment(artifact.content, title=name, embed=False)
        entity_data: dict = {
            "pod_id": pod_id,
            "user_id": user_id,
            "name": normalize_resource_name(name),
            "public_slug": public_slug or name,
            "description": description,
        }
        if visibility is not None:
            entity_data["visibility"] = visibility
        app = await self.create_app_with_context(
            AppEntity(**entity_data), user_id, ctx=ctx
        )
        return await self.upload_bundle(
            pod_id,
            app.name,
            user_id,
            source_archive_bytes=None,
            dist_archive_bytes=self._single_index_html_zip(document),
            ctx=ctx,
        )

    async def list_apps(
        self,
        pod_id: UUID,
        user_id: UUID,
        limit: int = 100,
        cursor: str | None = None,
        ctx: Context | None = None,
    ) -> tuple[list[AppEntity], str | None]:
        if ctx is not None:
            await ctx.require(Permissions.APP_READ, ResourceRef.pod(pod_id))
        else:
            raise RuntimeError("Context is required for app listing")
        return await self.repository.list_visible_by_pod(
            pod_id,
            ctx,
            limit=limit,
            cursor=cursor,
        )

    async def get_app_by_name(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        raise_not_found: bool = False,
        ctx: Context | None = None,
    ) -> AppEntity | None:
        app = await self.repository.get_by_name(pod_id, name, ctx=ctx)
        if not app:
            if raise_not_found:
                raise AppNotFoundError(f"App {name} not found")
            return None

        if ctx is not None:
            await ctx.require(Permissions.APP_READ, ResourceRef.app(pod_id, app.id))
        else:
            await self._require_pod_permission(
                pod_id=pod_id,
                user_id=user_id,
                required_role=PodRole.VIEWER,
                message=f"User {user_id} does not have access to pod {pod_id}",
                resource_type=ResourceType.APP,
                resource_id=app.id,
                ctx=ctx,
            )

        return app

    async def update_app(
        self,
        pod_id: UUID,
        name: str,
        update_entity: AppUpdateEntity,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> AppEntity:
        app = await self.repository.get_by_name(pod_id, name, ctx=ctx)
        if not app:
            raise AppNotFoundError(f"App {name} not found")

        if ctx is not None:
            await ctx.require(Permissions.APP_UPDATE, ResourceRef.app(pod_id, app.id))
        else:
            await self._require_pod_permission(
                pod_id=pod_id,
                user_id=user_id,
                required_role=PodRole.EDITOR,
                message=f"User {user_id} does not have editor access to pod {pod_id}",
                resource_type=ResourceType.APP,
                resource_id=app.id,
                ctx=ctx,
            )

        if update_entity.description is not None:
            app.description = update_entity.description
        if update_entity.public_slug is not None:
            public_slug = normalize_public_slug(update_entity.public_slug)
            if not public_slug:
                raise AppValidationError("public_slug cannot be empty")
            await self._validate_unique_public_slug(
                public_slug=public_slug,
                current_app_id=app.id,
            )
            app.public_slug = public_slug
        if update_entity.visibility is not None:
            app.visibility = self._normalize_visibility_value(update_entity.visibility).value

        updated = await self.repository.update(app)
        if ctx is not None:
            refreshed = await self.repository.get_by_name(pod_id, name, ctx=ctx)
            return refreshed or updated
        return updated

    async def delete_app(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        app = await self.repository.get_by_name(pod_id, name)
        if not app:
            raise AppNotFoundError(f"App {name} not found")

        if ctx is not None:
            await ctx.require(Permissions.APP_DELETE, ResourceRef.app(pod_id, app.id))
        else:
            await self._require_pod_permission(
                pod_id=pod_id,
                user_id=user_id,
                required_role=PodRole.ADMIN,
                message=f"User {user_id} does not have admin access to pod {pod_id}",
                resource_type=ResourceType.APP,
                resource_id=app.id,
                ctx=ctx,
            )

        storage = self.file_manager_factory(app.id)
        releases = await self.repository.list_releases(app.id)
        if app.source_archive_path:
            await self._delete_file_if_present(storage, app.source_archive_path)
        for release in releases:
            await self._delete_release_files(storage, release)
        await storage.delete_prefix("")
        await self.repository.delete(app.id)

    async def upload_bundle(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        source_archive_bytes: bytes | None,
        dist_archive_bytes: bytes | None,
        ctx: Context | None = None,
    ) -> AppEntity:
        app = await self.repository.get_by_name(pod_id, name)
        if not app:
            raise AppNotFoundError(f"App {name} not found")

        if ctx is not None:
            await ctx.require(Permissions.APP_UPDATE, ResourceRef.app(pod_id, app.id))
        else:
            await self._require_pod_permission(
                pod_id=pod_id,
                user_id=user_id,
                required_role=PodRole.EDITOR,
                message=f"User {user_id} does not have editor access to pod {pod_id}",
                resource_type=ResourceType.APP,
                resource_id=app.id,
                ctx=ctx,
            )

        if source_archive_bytes is None and dist_archive_bytes is None:
            raise AppValidationError("Provide source_archive and/or dist_archive")

        storage = self.file_manager_factory(app.id)

        if source_archive_bytes is not None:
            path = "source/archive.zip"
            await storage.write_file(path, source_archive_bytes)
            app.source_archive_path = path

        if dist_archive_bytes is not None:
            bundle = load_app_dist_bundle(dist_archive_bytes)
            version = hashlib.sha256(dist_archive_bytes).hexdigest()
            release = await self.repository.get_release_by_version(app.id, version)
            if release is None:
                release_root = f"releases/{version}/dist/"
                for item in bundle.files:
                    await storage.write_file(f"{release_root}{item.path}", item.content)

                dist_archive_path = f"{release_root}archive.zip"
                await storage.write_file(dist_archive_path, dist_archive_bytes)

                release = await self.repository.create_release(
                    AppReleaseEntity(
                        app_id=app.id,
                        version=version,
                        dist_root_path=release_root,
                        dist_archive_path=dist_archive_path,
                    )
                )

            app.current_release_id = release.id
            app.status = AppStatus.READY

        app.user_id = user_id
        return await self.repository.update(app)

    async def get_app_asset(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        asset_path: str | None,
        request_etag: str | None = None,
        ctx: Context | None = None,
    ) -> AppAssetDocument:
        app = await self.get_app_by_name(
            pod_id,
            name,
            user_id,
            raise_not_found=True,
            ctx=ctx,
        )
        assert app is not None
        return await self._build_asset_document(
            app,
            raise_not_found_name=name,
            asset_path=asset_path,
            request_etag=request_etag,
        )

    async def resolve_app_asset(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        asset_path: str | None,
        request_etag: str | None = None,
        ctx: Context | None = None,
    ) -> _AssetReadInputs | AppAssetDocument:
        """DB+authz phase for serving an authed app asset. Call inside a short UoW;
        then call read_app_asset (storage) outside it. Returns a not-modified
        document directly on a 304."""
        app = await self.get_app_by_name(
            pod_id, name, user_id, raise_not_found=True, ctx=ctx
        )
        assert app is not None
        return await self._resolve_asset_document(
            app,
            raise_not_found_name=name,
            asset_path=asset_path,
            request_etag=request_etag,
        )

    async def resolve_app_asset_by_public_slug(
        self,
        public_slug: str,
        *,
        asset_path: str | None,
        request_etag: str | None = None,
    ) -> _AssetReadInputs | AppAssetDocument:
        """DB phase for serving a public (unauthenticated) app asset by slug."""
        app = await self.repository.get_by_public_slug(public_slug)
        if not app:
            raise AppNotFoundError(f"App with public slug '{public_slug}' not found")
        return await self._resolve_asset_document(
            app,
            raise_not_found_name=public_slug,
            asset_path=asset_path,
            request_etag=request_etag,
        )

    async def get_app_asset_public(
        self,
        pod_id: UUID,
        name: str,
        *,
        asset_path: str | None,
        request_etag: str | None = None,
    ) -> AppAssetDocument:
        """Fetch an app asset without a permission check — for unauthenticated serving."""
        app = await self.repository.get_by_name(pod_id, name)
        if not app:
            raise AppNotFoundError(f"App '{name}' not found")
        return await self._build_asset_document(
            app,
            raise_not_found_name=name,
            asset_path=asset_path,
            request_etag=request_etag,
        )

    async def get_app_asset_by_public_slug(
        self,
        public_slug: str,
        *,
        asset_path: str | None,
        request_etag: str | None = None,
    ) -> AppAssetDocument:
        app = await self.repository.get_by_public_slug(public_slug)
        if not app:
            raise AppNotFoundError(f"App with public slug '{public_slug}' not found")
        return await self._build_asset_document(
            app,
            raise_not_found_name=public_slug,
            asset_path=asset_path,
            request_etag=request_etag,
        )

    async def get_app_source_archive(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> bytes:
        app_id, archive_path = await self.resolve_source_archive(
            pod_id, name, user_id, ctx=ctx
        )
        return await self.read_archive(app_id, archive_path)

    async def get_app_dist_archive(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> bytes:
        app_id, archive_path = await self.resolve_dist_archive(
            pod_id, name, user_id, ctx=ctx
        )
        return await self.read_archive(app_id, archive_path)

    async def resolve_source_archive(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> tuple[UUID, str]:
        """Resolve + authorize the source archive's storage location (DB access).
        Pair with ``read_archive`` so the archive read runs after the DB session
        closes, not while a pooled connection is held for the whole transfer."""
        app = await self.get_app_by_name(
            pod_id,
            name,
            user_id,
            raise_not_found=True,
            ctx=ctx,
        )
        assert app is not None
        if not app.source_archive_path:
            raise AppNotFoundError(f"Source archive not found for app '{name}'")
        return app.id, app.source_archive_path

    async def resolve_dist_archive(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> tuple[UUID, str]:
        """Resolve + authorize the dist archive's storage location (DB access).
        Pair with ``read_archive`` (see ``resolve_source_archive``)."""
        app = await self.get_app_by_name(
            pod_id,
            name,
            user_id,
            raise_not_found=True,
            ctx=ctx,
        )
        assert app is not None
        release = await self._get_current_release(app, raise_not_found_name=name)
        if not release.dist_archive_path:
            raise AppNotFoundError(f"Dist archive not found for app '{name}'")
        return app.id, release.dist_archive_path

    async def read_archive(self, app_id: UUID, archive_path: str) -> bytes:
        """Read an archive's bytes from app storage for an already-resolved app.
        Storage only — **no DB session** — safe to call after the resolving UoW
        closed."""
        storage = self.file_manager_factory(app_id)
        content = await storage.read_file(archive_path)
        if isinstance(content, str):
            return content.encode("utf-8")
        return content

    def _normalize_app_visibility(self, entity: AppEntity) -> None:
        entity.visibility = self._normalize_visibility_value(entity.visibility).value

    @staticmethod
    def _normalize_visibility_value(value: str | None) -> ResourceVisibility:
        normalized = str(value or ResourceVisibility.POD.value).strip().upper()
        if normalized in PERSONAL_VISIBILITY_VALUES or normalized in {"PRIVATE", "OWNER"}:
            return ResourceVisibility.PERSONAL
        if normalized == "RESTRICTED":
            return ResourceVisibility.RESTRICTED
        if normalized == "PUBLIC":
            return ResourceVisibility.PUBLIC
        if normalized in POD_VISIBILITY_VALUES:
            return ResourceVisibility.POD
        raise AppValidationError("Unsupported app visibility")
