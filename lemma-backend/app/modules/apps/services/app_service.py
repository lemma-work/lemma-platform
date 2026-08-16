"""App service."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import structlog

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.api.uploads import upload_source_sha256
from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
    normalize_resource_visibility,
)
from app.core.html_document import wrap_html_fragment
from app.core.ports.widget_content import WidgetArtifact
from app.core.widget_html_validation import lint_app_html
from app.core.authorization.permissions import Permissions
from app.core.helpers.slug import normalize_public_slug, normalize_resource_name
from app.modules.apps.domain.events import AppPublishedEvent
from app.modules.apps.domain.entities import (
    AppAssetDocument,
    AppEntity,
    AppReleaseEntity,
    AppStatus,
    AppUpdateEntity,
)
from app.modules.apps.domain.branding import AppBrandingEntitlementPort
from app.modules.apps.domain.errors import (
    AppConflictError,
    AppNotFoundError,
    AppValidationError,
)
from app.modules.apps.domain.ports import (
    AppRepositoryPort,
    AppStorageFactoryPort,
)
from app.modules.apps.services.app_asset_resolver import AppAssetResolver
from app.modules.apps.services.app_dist_bundle import (
    load_app_dist_bundle,
    single_index_html_zip,
)
from app.modules.apps.services.app_storage_phase import (
    AppStoragePhase,
    _AppDeletionCleanup,
    _AssetReadInputs,
    _UploadPlan,
    _WrittenBundle,
)
from app.modules.pod.contracts import PodRole
from app.core.concurrency.offload import run_blocking

logger = structlog.get_logger()


class AppService:
    def __init__(
        self,
        app_repository: AppRepositoryPort,
        file_manager_factory: AppStorageFactoryPort,
        authorization_service: object,
        app_branding_entitlement: AppBrandingEntitlementPort | None = None,
    ):
        self.repository = app_repository
        self.file_manager_factory = file_manager_factory
        self.authorization_service = authorization_service
        self._asset_resolver = AppAssetResolver(
            app_repository,
            app_branding_entitlement,
        )
        # Storage side of the asset/archive/bundle/delete sagas, on an object
        # that holds NO repository (DB-free by construction).
        self._storage_phase = AppStoragePhase(file_manager_factory)

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


    async def read_app_asset(self, inputs: _AssetReadInputs) -> AppAssetDocument:
        """Storage phase: read the asset bytes — delegated to the repo-free
        ``AppStoragePhase`` (holds no DB connection)."""
        return await self._storage_phase.read_asset(inputs)

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
            refreshed = await self.repository.get_by_name(
                entity.pod_id, entity.name, ctx=ctx
            )
            return refreshed or created
        return created

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
        The widget and the app share one source artifact at two lifecycle stages:
        the stored fragment is preserved, wrapped as a standalone document (no
        embed bridge or conversation padding), and deployed as the app's bundle.
        """
        for issue in lint_app_html(artifact.content):
            logger.debug(
                "apps.app_service.app_html_lint.diagnostic", pod_id=str(pod_id)
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
        # The document IS the source for a promoted widget -- no build step sits
        # behind it -- so it ships as both. Uploading dist only left the app
        # source-less, and a pod bundle then exported its build without its code.
        archive = await run_blocking(single_index_html_zip, document)
        return await self.upload_bundle(
            pod_id,
            app.name,
            user_id,
            source_archive_bytes=archive,
            dist_archive_bytes=archive,
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
            app.visibility = self._normalize_visibility_value(
                update_entity.visibility
            ).value

        updated = await self.repository.update(app)
        if ctx is not None:
            refreshed = await self.repository.get_by_name(pod_id, name, ctx=ctx)
            return refreshed or updated
        return updated

    async def resolve_delete_app(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> _AppDeletionCleanup:
        """Authorize + delete the app row (DB only). Returns the storage paths to
        purge so the caller can clean up AFTER this UoW commits — the storage
        cleanup (potentially many objects) must not hold a connection."""
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
        releases = await self.repository.list_releases(app.id)
        await self.repository.delete(app.id)
        return _AppDeletionCleanup(
            app_id=app.id,
            source_archive_path=app.source_archive_path,
            releases=tuple(releases),
        )

    async def cleanup_app_storage(self, cleanup: _AppDeletionCleanup) -> None:
        """Delete an app's stored bytes — delegated to the repo-free
        ``AppStoragePhase`` (holds no DB connection). Call after
        resolve_delete_app's UoW has committed."""
        await self._storage_phase.cleanup_storage(cleanup)

    async def resolve_upload_bundle(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        has_source: bool,
        dist_archive_bytes: bytes | Path | None,
        ctx: Context | None = None,
    ) -> _UploadPlan:
        """Authorize + dedup (DB only). The storage writes happen outside this UoW
        via write_bundle_storage; finalize_upload_bundle then persists."""
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
        if not has_source and dist_archive_bytes is None:
            raise AppValidationError("Provide source_archive and/or dist_archive")

        version: str | None = None
        release_root: str | None = None
        existing_release_id: UUID | None = None
        needs_dist_write = False
        revives_release = False
        if dist_archive_bytes is not None:
            # Validate the bundle up front (raises AppValidationError on a missing
            # root index.html), regardless of dedup — matches prior behavior and
            # ensures no storage write happens for an invalid bundle.
            # Both are thread offloads over the whole archive. The method's own
            # docstring calls this phase "DB only", which it was not.
            async with connection_released(getattr(self.repository, "session", None)):
                await run_blocking(load_app_dist_bundle, dist_archive_bytes)
                version = await run_blocking(
                    upload_source_sha256, dist_archive_bytes
                )
            release_root = f"releases/{version}/dist/"
            existing = await self.repository.get_release_by_version(app.id, version)
            existing_release_id = existing.id if existing is not None else None
            # A matching digest is not proof the bytes are still there:
            # retention deletes them and keeps the row.
            revives_release = existing is not None and existing.is_pruned
            needs_dist_write = existing is None or revives_release
        return _UploadPlan(
            app_id=app.id,
            pod_id=pod_id,
            name=name,
            has_source=has_source,
            version=version,
            release_root=release_root,
            existing_release_id=existing_release_id,
            needs_dist_write=needs_dist_write,
            revives_release=revives_release,
        )

    async def write_bundle_storage(
        self,
        plan: _UploadPlan,
        source_archive_bytes: bytes | Path | None,
        dist_archive_bytes: bytes | Path | None,
    ) -> _WrittenBundle:
        """Write uploaded bytes to storage — delegated to the repo-free
        ``AppStoragePhase`` (holds no DB connection). Call between
        resolve_upload_bundle and finalize_upload_bundle."""
        return await self._storage_phase.write_bundle(
            plan, source_archive_bytes, dist_archive_bytes
        )

    async def finalize_upload_bundle(
        self, plan: _UploadPlan, written: _WrittenBundle, user_id: UUID
    ) -> AppEntity:
        """Persist the release + app pointer (DB only) after the storage writes."""
        app = await self.repository.get_by_name(plan.pod_id, plan.name)
        if not app:
            raise AppNotFoundError(f"App {plan.name} not found")
        release_id = plan.existing_release_id
        if plan.needs_dist_write:
            # Upsert: a redeploy of a pruned build revives that release
            # rather than minting a second row for the same digest.
            release = await self.repository.record_release(
                AppReleaseEntity(
                    app_id=app.id,
                    version=plan.version,
                    dist_root_path=plan.release_root,
                    dist_archive_path=written.dist_archive_path,
                    source_archive_path=written.source_path,
                    source_digest=written.source_digest,
                    created_by=user_id,
                )
            )
            release_id = release.id
        elif release_id is not None and written.source_path is not None:
            # Deduped onto an existing release: same dist bytes, same release.
            # The source can still differ (a comment-only edit compiles to the
            # same output), and the newest is the better answer to what built it.
            await self.repository.attach_release_source(
                release_id,
                source_archive_path=written.source_path,
                source_digest=written.source_digest,
            )
        if written.source_path is not None:
            app.source_archive_path = written.source_path
        newly_published = plan.version is not None and app.status is not AppStatus.READY
        if plan.version is not None:
            app.current_release_id = release_id
            app.status = AppStatus.READY
        app.user_id = user_id
        updated = await self.repository.update(app)
        if newly_published:
            # The transition, not the state: a re-upload of an already-published
            # app is a new release, and counting those would make this track
            # deploy frequency rather than how many pods have shipped something.
            self.repository.uow.collect_events(
                [
                    AppPublishedEvent(
                        app_id=updated.id, pod_id=updated.pod_id, user_id=user_id
                    )
                ]
            )
        return updated

    async def cleanup_written_bundle(
        self, plan: _UploadPlan, written: _WrittenBundle
    ) -> None:
        await self._storage_phase.cleanup_written_bundle(plan, written)

    async def upload_bundle(
        self,
        pod_id: UUID,
        name: str,
        user_id: UUID,
        *,
        source_archive_bytes: bytes | Path | None,
        dist_archive_bytes: bytes | Path | None,
        ctx: Context | None = None,
    ) -> AppEntity:
        # Back-compat single-call path (holds the connection across storage). The
        # controller uses resolve/write/finalize so storage holds no connection.
        from app.modules.apps.services.archive_validation import inspect_app_archive

        if source_archive_bytes is not None:
            # Offloaded: opens the zip and walks its whole member list. The
            # controller path already offloads this (``app_use_cases.upload_bundle``);
            # this back-compat entry point was calling it inline.
            await run_blocking(
                inspect_app_archive,
                source_archive_bytes,
                label="Source archive",
                limiter="cpu_bound",
            )
        plan = await self.resolve_upload_bundle(
            pod_id,
            name,
            user_id,
            has_source=source_archive_bytes is not None,
            dist_archive_bytes=dist_archive_bytes,
            ctx=ctx,
        )
        written = await self.write_bundle_storage(
            plan, source_archive_bytes, dist_archive_bytes
        )
        try:
            return await self.finalize_upload_bundle(plan, written, user_id)
        except BaseException:
            await self.cleanup_written_bundle(plan, written)
            raise

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
        return await self._asset_resolver.resolve(
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
        release_ref: str | None = None,
    ) -> _AssetReadInputs | AppAssetDocument:
        """DB phase for serving a public (unauthenticated) app asset by slug.

        ``release_ref`` serves a specific release instead of the live one, for
        the preview host ``<slug>--r7.<app_base_domain>``. See
        ``AppAssetResolver.preview_url`` for why it is a host and not a prefix.
        """
        app = await self.repository.get_by_public_slug(public_slug)
        if not app:
            raise AppNotFoundError(f"App with public slug '{public_slug}' not found")
        # No session reaches this route -- the ingress serves it to anonymous
        # browsers by host -- so only an app published to everyone belongs here.
        # Apps default to POD, which made the default "exposed to anyone who
        # guesses the slug". An unrecognized stored value is not PUBLIC either.
        # Report it as missing rather than forbidden: a 403 would confirm the
        # slug exists to a caller who only guessed it.
        if normalize_resource_visibility(app.visibility) is not ResourceVisibility.PUBLIC:
            raise AppNotFoundError(f"App with public slug '{public_slug}' not found")
        release = None
        public_url = self._asset_resolver.public_url(app)
        if release_ref is not None:
            from app.modules.apps.services.app_release_service import resolve_preview

            release, public_url = await resolve_preview(
                self.repository, self._asset_resolver, app, release_ref
            )
        return await self._asset_resolver.resolve(
            app,
            raise_not_found_name=public_slug,
            asset_path=asset_path,
            request_etag=request_etag,
            public_url=public_url,
            release=release,
        )

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
        release = await self._asset_resolver.current_release(
            app,
            raise_not_found_name=name,
        )
        if not release.dist_archive_path:
            raise AppNotFoundError(f"Dist archive not found for app '{name}'")
        return app.id, release.dist_archive_path

    async def read_archive(self, app_id: UUID, archive_path: str) -> bytes:
        """Read an archive's bytes — delegated to the repo-free ``AppStoragePhase``
        (holds no DB connection). Safe after the resolving UoW closed."""
        return await self._storage_phase.read_archive(app_id, archive_path)

    def _normalize_app_visibility(self, entity: AppEntity) -> None:
        entity.visibility = self._normalize_visibility_value(entity.visibility).value

    @staticmethod
    def _normalize_visibility_value(value: str | None) -> ResourceVisibility:
        # Apps reject an unrecognized value rather than defaulting, so a typo in
        # a bundle surfaces at import instead of silently publishing narrower.
        visibility = normalize_resource_visibility(value)
        if visibility is None:
            raise AppValidationError("Unsupported app visibility")
        return visibility
