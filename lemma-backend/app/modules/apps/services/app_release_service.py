"""Release history for an app: list it, resolve one, make one live.

Every bundle upload has always written a release row and kept its bytes, so
rolling back needs no new storage -- only a way to name a release and move
``apps.current_release_id``. That is what lives here.

This is a separate service rather than more methods on :class:`~app.modules.
apps.services.app_service.AppService` because that file is already at the
architecture ratchet's per-file ceiling. The split is a real seam anyway:
``AppService`` owns deploying an app, this owns which deployed build is live.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity, AppStatus
from app.modules.apps.domain.errors import (
    AppNotFoundError,
    AppReleaseNotFoundError,
    AppReleasePrunedError,
)
from app.modules.apps.domain.ports import AppRepositoryPort
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReleaseListing:
    """One release plus the fact only the app row can answer."""

    release: AppReleaseEntity
    is_live: bool


@dataclass(frozen=True, slots=True)
class ReleaseHistory:
    """An app's releases, newest first, with the slug their previews hang off."""

    app_public_slug: str
    items: list[ReleaseListing]


def parse_release_ref(ref: str) -> tuple[int | None, str | None]:
    """Split a release reference into ``(release_number, digest_prefix)``.

    A reference is either the counter people read and type -- ``7`` or ``v7``,
    matching the preview host ``<slug>--r7`` -- or a prefix of the dist digest,
    for the cases where someone has the hash and not the number.
    """
    candidate = (ref or "").strip().lower()
    if not candidate:
        raise AppReleaseNotFoundError("No release was named")
    numeric = candidate[1:] if candidate[0] in {"v", "r"} else candidate
    if numeric.isdigit():
        return int(numeric), None
    return None, candidate.removeprefix("sha256:")


class AppReleaseService:
    def __init__(self, app_repository: AppRepositoryPort):
        self.repository = app_repository

    async def _load_app(
        self,
        pod_id: UUID,
        app_name: str,
        *,
        permission: str,
        ctx: Context,
    ) -> AppEntity:
        app = await self.repository.get_by_name(pod_id, app_name, ctx=ctx)
        if not app:
            raise AppNotFoundError(f"App {app_name} not found")
        assert app.id is not None
        await ctx.require(permission, ResourceRef.app(pod_id, app.id))
        return app

    async def resolve_release(
        self, app: AppEntity, ref: str, *, allow_pruned: bool = False
    ) -> AppReleaseEntity:
        """Resolve a release reference against one app.

        A pruned release resolves but is refused by default: callers that serve
        or promote need the bytes, and only the listing wants to see it.
        """
        assert app.id is not None
        number, digest_prefix = parse_release_ref(ref)
        release: AppReleaseEntity | None = None
        if number is not None:
            release = await self.repository.get_release_by_number(app.id, number)
            # A digest is hex, so a short prefix can be all decimal digits and
            # read as a release number (roughly one in forty 8-character
            # prefixes). Falling through to a digest lookup when no release
            # carries that number resolves it without making people prefix every
            # number with `v`.
            if release is None:
                digest_prefix = ref.strip().lower()
        if release is None and digest_prefix is not None:
            matches = [
                candidate
                for candidate in await self.repository.list_releases(app.id)
                if candidate.version.startswith(digest_prefix)
            ]
            # An ambiguous prefix is refused rather than resolved to "the newest
            # match": promoting the wrong build is not a recoverable mistake.
            if len(matches) > 1:
                raise AppReleaseNotFoundError(
                    f"Release '{ref}' is ambiguous -- it matches "
                    f"{len(matches)} releases. Use the full digest or the "
                    "release number."
                )
            release = matches[0] if matches else None
        if release is None:
            raise AppReleaseNotFoundError(
                f"App '{app.name}' has no release '{ref}'"
            )
        if release.is_pruned and not allow_pruned:
            raise AppReleasePrunedError(
                f"Release v{release.release_number} of '{app.name}' was removed "
                "by retention, so its build can no longer be served or promoted."
            )
        return release

    async def list_releases(
        self, pod_id: UUID, app_name: str, *, ctx: Context
    ) -> ReleaseHistory:
        app = await self._load_app(
            pod_id, app_name, permission=Permissions.APP_READ, ctx=ctx
        )
        assert app.id is not None
        releases = await self.repository.list_releases(app.id)
        return ReleaseHistory(
            app_public_slug=app.public_slug,
            items=[
                ReleaseListing(
                    release=release,
                    is_live=release.id == app.current_release_id,
                )
                for release in releases
            ],
        )

    async def promote_release(
        self, pod_id: UUID, app_name: str, ref: str, *, ctx: Context
    ) -> AppReleaseEntity:
        """Make an existing release live. DB only -- no bytes move.

        The app's source pointer follows the release. Leaving it behind would
        mean an export taken after a rollback shipped source that never produced
        the running build.
        """
        app = await self._load_app(
            pod_id, app_name, permission=Permissions.APP_UPDATE, ctx=ctx
        )
        assert app.id is not None
        release = await self.resolve_release(app, ref)

        if release.id == app.current_release_id:
            return release

        app.current_release_id = release.id
        app.status = AppStatus.READY
        # Only follow the release's own source. A release backfilled before this
        # column existed has none, and overwriting the app's working pointer with
        # NULL would lose the source entirely.
        if release.source_archive_path is not None:
            app.source_archive_path = release.source_archive_path
        await self.repository.update(app)
        logger.info(
            "apps.app_release_service.release_promoted",
            app_id=str(app.id),
            pod_id=str(pod_id),
            release_number=release.release_number,
            version=release.version,
        )
        return release


async def resolve_preview(repository, asset_resolver, app, release_ref):
    """The release a preview host asks for, plus the URL that names it.

    Lives here rather than on ``AppService`` so release-reference parsing stays
    in one place: the serving path must not grow its own answer to "is this a
    number or a digest prefix". The preview identifies itself as a preview, so
    the branding badge and the og:url of a shared link do not claim to be the
    live app at a build nobody has promoted.
    """
    release = await AppReleaseService(repository).resolve_release(app, release_ref)
    return release, asset_resolver.preview_url(app, release)
