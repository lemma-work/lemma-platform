"""Unit tests for the shared HTML asset response builders."""

from __future__ import annotations

from uuid import uuid4

from app.core.api.html_response import (
    build_asset_response,
    build_injected_html_response,
)


def test_injected_html_response_injects_config_and_no_store():
    pod_id = uuid4()
    resp = build_injected_html_response(
        "<html><head></head><body>x</body></html>", pod_id
    )
    body = resp.body.decode()
    assert "data-lemma-runtime-config" in body
    assert str(pod_id) in body
    assert resp.headers["cache-control"] == "no-store"
    assert resp.media_type == "text/html; charset=utf-8"


def test_injected_html_response_custom_cache_control():
    resp = build_injected_html_response(
        "<html></html>", uuid4(), cache_control="public, max-age=60"
    )
    assert resp.headers["cache-control"] == "public, max-age=60"


def test_asset_response_entrypoint_is_no_cache_with_etag():
    resp = build_asset_response(
        content=b"<html></html>",
        media_type="text/html",
        etag='"v1"',
        is_entrypoint=True,
    )
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, no-cache"
    assert resp.headers["etag"] == '"v1"'


def test_asset_response_static_is_immutable():
    resp = build_asset_response(
        content=b"console.log(1)",
        media_type="application/javascript",
        etag='"v1"',
        is_entrypoint=False,
    )
    assert "immutable" in resp.headers["cache-control"]


def test_asset_response_not_modified_returns_304():
    resp = build_asset_response(
        content=None,
        media_type="text/html",
        etag='"v1"',
        is_entrypoint=True,
        not_modified=True,
    )
    assert resp.status_code == 304
    assert resp.headers["etag"] == '"v1"'
