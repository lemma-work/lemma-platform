"""Adapt a ``AppAssetDocument`` onto the shared HTML asset response builder."""

from __future__ import annotations

from fastapi.responses import Response

from app.core.api.html_response import build_asset_response
from app.modules.apps.domain.entities import AppAssetDocument


def app_asset_response(asset: AppAssetDocument) -> Response:
    return build_asset_response(
        content=asset.content,
        media_type=asset.media_type,
        etag=asset.etag,
        is_entrypoint=asset.is_entrypoint,
        not_modified=asset.not_modified,
        extra_headers=asset.headers,
    )
