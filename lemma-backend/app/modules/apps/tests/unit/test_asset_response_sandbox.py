"""An app's HTML read from the API's own host never runs there (H20).

On the API's origin the app's script would be indistinguishable from the
viewer's own requests, so its pod pin (``core/app_pin``) would not hold.
"""

from __future__ import annotations

from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.domain.entities import AppAssetDocument


def _asset(**extra) -> AppAssetDocument:
    return AppAssetDocument(
        content=b"<html></html>",
        media_type="text/html",
        etag='"e"',
        is_entrypoint=True,
        **extra,
    )


def test_app_html_on_the_api_host_is_sandboxed():
    response = app_asset_response(_asset(), sandboxed=True)

    assert response.headers["content-security-policy"] == "sandbox"


def test_the_sandbox_keeps_the_assets_own_headers():
    response = app_asset_response(
        _asset(headers={"Service-Worker-Allowed": "/"}), sandboxed=True
    )

    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["content-security-policy"] == "sandbox"


def test_the_app_on_its_own_host_is_not_sandboxed():
    assert "content-security-policy" not in app_asset_response(_asset()).headers
