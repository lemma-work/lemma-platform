"""Unit tests for the public browser-SDK + UI bundle routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.apps.api.controllers import public_sdk_controller as sdk


@pytest.mark.asyncio
async def test_serves_sdk_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "lemma-client.js"
    bundle.write_text("window.LemmaClient = {};", encoding="utf-8")
    monkeypatch.setattr(
        sdk, "settings", SimpleNamespace(resolve_browser_sdk_path=lambda: bundle)
    )
    sdk._bundle_cache.clear()

    response = await sdk.get_browser_sdk()

    assert response.status_code == 200
    assert response.media_type == "application/javascript"
    assert response.headers["cache-control"] == "public, no-cache"
    assert b"window.LemmaClient" in response.body


@pytest.mark.asyncio
async def test_missing_bundle_returns_404(monkeypatch):
    monkeypatch.setattr(
        sdk, "settings", SimpleNamespace(resolve_browser_sdk_path=lambda: None)
    )
    sdk._bundle_cache.clear()

    with pytest.raises(HTTPException) as exc:
        await sdk.get_browser_sdk()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_rereads_when_bundle_changes(tmp_path, monkeypatch):
    bundle = tmp_path / "lemma-client.js"
    bundle.write_text("v1", encoding="utf-8")
    monkeypatch.setattr(
        sdk, "settings", SimpleNamespace(resolve_browser_sdk_path=lambda: bundle)
    )
    sdk._bundle_cache.clear()

    first = await sdk.get_browser_sdk()
    assert first.body == b"v1"

    # Rewrite with a newer mtime; the (path, mtime) cache key must invalidate.
    import os

    bundle.write_text("v2-updated", encoding="utf-8")
    os.utime(bundle, (bundle.stat().st_atime + 10, bundle.stat().st_mtime + 10))

    second = await sdk.get_browser_sdk()
    assert second.body == b"v2-updated"


@pytest.mark.asyncio
async def test_serves_ui_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "lemma-ui.js"
    bundle.write_text(
        "customElements.define('lemma-agent-task', class {});", encoding="utf-8"
    )
    monkeypatch.setattr(
        sdk, "settings", SimpleNamespace(resolve_browser_ui_path=lambda: bundle)
    )
    sdk._bundle_cache.clear()

    response = await sdk.get_browser_ui()

    assert response.status_code == 200
    assert response.media_type == "application/javascript"
    assert b"lemma-agent-task" in response.body


@pytest.mark.asyncio
async def test_missing_ui_bundle_returns_404(monkeypatch):
    monkeypatch.setattr(
        sdk, "settings", SimpleNamespace(resolve_browser_ui_path=lambda: None)
    )
    sdk._bundle_cache.clear()

    with pytest.raises(HTTPException) as exc:
        await sdk.get_browser_ui()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_client_and_ui_bundles_coexist_in_cache(tmp_path, monkeypatch):
    """Serving one bundle must not evict the other from the shared cache."""
    client = tmp_path / "lemma-client.js"
    client.write_text("CLIENT", encoding="utf-8")
    ui = tmp_path / "lemma-ui.js"
    ui.write_text("UI", encoding="utf-8")
    monkeypatch.setattr(
        sdk,
        "settings",
        SimpleNamespace(
            resolve_browser_sdk_path=lambda: client,
            resolve_browser_ui_path=lambda: ui,
        ),
    )
    sdk._bundle_cache.clear()

    assert (await sdk.get_browser_sdk()).body == b"CLIENT"
    assert (await sdk.get_browser_ui()).body == b"UI"
    # Both reads should now be cached under distinct (path, mtime) keys.
    assert len(sdk._bundle_cache) == 2
    assert (await sdk.get_browser_sdk()).body == b"CLIENT"
