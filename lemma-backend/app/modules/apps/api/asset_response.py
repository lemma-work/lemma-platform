"""Adapt a ``AppAssetDocument`` onto the shared HTML asset response builder."""

from __future__ import annotations

from fastapi.responses import Response

from app.core.api.html_response import build_asset_response
from app.modules.apps.domain.entities import AppAssetDocument

#: An app's files read from the API's own host (``/pods/{pod_id}/apps/{name}/
#: assets``) are for downloading and inspecting, never for running. Opened there,
#: the app's script would run as the API's own origin -- whose same-origin
#: requests the API cannot tell from the viewer's own, so the app's pod pin
#: (H20, ``core/app_pin``) would not hold. ``sandbox`` gives the document an
#: opaque origin and no scripts; the app runs on its own host.
API_HOST_SANDBOX = {"Content-Security-Policy": "sandbox"}


def app_asset_response(asset: AppAssetDocument, *, sandboxed: bool = False) -> Response:
    headers = {**(asset.headers or {}), **(API_HOST_SANDBOX if sandboxed else {})}
    return build_asset_response(
        content=asset.content,
        media_type=asset.media_type,
        etag=asset.etag,
        is_entrypoint=asset.is_entrypoint,
        not_modified=asset.not_modified,
        extra_headers=headers or None,
        private=asset.private,
    )
