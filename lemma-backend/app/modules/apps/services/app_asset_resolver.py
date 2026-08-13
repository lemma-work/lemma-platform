"""Resolve app asset requests before the storage phase.

This service owns release lookup, entrypoint presentation policy, and ETag
calculation. Keeping those concerns outside ``AppService`` prevents the main
application service from growing with each new hosted-app presentation feature.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from urllib.parse import urlparse

import structlog

from app.core import runtime_config
from app.core.config import settings
from app.modules.apps.domain.branding import AppBrandingEntitlementPort
from app.modules.apps.domain.entities import (
    AppAssetDocument,
    AppEntity,
    AppReleaseEntity,
)
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.domain.ports import AppRepositoryPort
from app.modules.apps.services.app_storage_phase import _AssetReadInputs

logger = structlog.get_logger()


class AppAssetResolver:
    def __init__(
        self,
        repository: AppRepositoryPort,
        branding_entitlement: AppBrandingEntitlementPort | None = None,
    ) -> None:
        self.repository = repository
        self.branding_entitlement = branding_entitlement

    @staticmethod
    def public_url(app: AppEntity) -> str:
        scheme = urlparse(settings.api_url).scheme or "https"
        return f"{scheme}://{app.public_slug}.{settings.app_base_domain}"

    @staticmethod
    def preview_url(app: AppEntity, release: AppReleaseEntity) -> str:
        """The canonical host a specific release is previewed at.

        ``--`` is the separator because ``normalize_public_slug`` collapses runs
        of ``-``, so no real slug can contain one: the label always splits back
        into exactly the slug and the release, however many hyphens the slug has.
        The release number is used even when the caller addressed the release by
        digest, so one release has one preview URL.
        """
        scheme = urlparse(settings.api_url).scheme or "https"
        return (
            f"{scheme}://{app.public_slug}--r{release.release_number}"
            f".{settings.app_base_domain}"
        )

    @staticmethod
    def _quote_etag(etag: str | None) -> str | None:
        if not etag:
            return None
        return f'"{etag}"'

    @staticmethod
    def _etag_matches(candidate: str | None, request_header: str | None) -> bool:
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
    def _normalize_asset_path(asset_path: str | None) -> str:
        normalized = (asset_path or "").replace("\\", "/").strip("/")
        if not normalized:
            return ""

        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise AppNotFoundError("App asset not found")
        return path.as_posix()

    async def current_release(
        self,
        app: AppEntity,
        *,
        raise_not_found_name: str,
    ) -> AppReleaseEntity:
        if not app.current_release_id:
            raise AppNotFoundError(f"Build not found for app '{raise_not_found_name}'")

        release = await self.repository.get_release(app.current_release_id)
        if not release:
            raise AppNotFoundError(
                f"Current release not found for app '{raise_not_found_name}'"
            )
        return release

    async def _branding(
        self,
        app: AppEntity,
        *,
        public_url: str | None,
        is_entrypoint: bool,
    ) -> dict[str, str] | None:
        if not is_entrypoint or not public_url or not settings.app_branding_enabled:
            return None
        if self.branding_entitlement is None:
            return runtime_config.build_app_branding(public_url)

        entitlement_result = (
            await asyncio.gather(
                self.branding_entitlement.can_remove_app_branding(pod_id=app.pod_id),
                return_exceptions=True,
            )
        )[0]
        if isinstance(entitlement_result, asyncio.CancelledError):
            raise entitlement_result
        if isinstance(entitlement_result, BaseException):
            logger.warning(
                "apps.app_asset_resolver.branding_entitlement.diagnostic",
                pod_id=str(app.pod_id),
                error_type=type(entitlement_result).__name__,
            )
            return runtime_config.build_app_branding(public_url)
        if entitlement_result:
            return None
        return runtime_config.build_app_branding(public_url)

    async def resolve(
        self,
        app: AppEntity,
        *,
        raise_not_found_name: str,
        asset_path: str | None,
        request_etag: str | None = None,
        public_url: str | None = None,
        release: AppReleaseEntity | None = None,
    ) -> _AssetReadInputs | AppAssetDocument:
        # `release` lets a preview host serve a build that is not live through
        # exactly this path -- one ETag rule, one branding rule, one runtime
        # config injection -- instead of a parallel serving implementation that
        # would drift from the live one.
        if release is None:
            release = await self.current_release(
                app,
                raise_not_found_name=raise_not_found_name,
            )
        normalized_asset_path = self._normalize_asset_path(asset_path)
        app_identity = runtime_config.build_runtime_app_identity(
            app.name,
            app.description,
            public_url,
        )
        is_entrypoint = runtime_config.is_runtime_config_entrypoint(
            normalized_asset_path
        )
        branding = await self._branding(
            app,
            public_url=public_url,
            is_entrypoint=is_entrypoint,
        )
        etag = (
            f"{release.version}."
            f"{runtime_config.runtime_config_token(app.pod_id, app=app_identity, branding=branding)}"
            if is_entrypoint
            else release.version
        )
        quoted_etag = self._quote_etag(etag)
        if self._etag_matches(etag, request_etag):
            return AppAssetDocument(
                etag=quoted_etag,
                not_modified=True,
                is_entrypoint=is_entrypoint,
            )

        return _AssetReadInputs(
            app_id=app.id,
            pod_id=app.pod_id,
            dist_root_path=release.dist_root_path,
            normalized_asset_path=normalized_asset_path,
            quoted_etag=quoted_etag,
            app=app_identity,
            branding=branding,
        )
