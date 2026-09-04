"""Public browser-SDK controller — serves the bundled Lemma client and the
opt-in UI bundle (unauthenticated).

No-build HTML apps cannot npm-import the SDK, so they load it from
``GET /public/sdk/lemma-client.js`` (exposes ``window.LemmaClient`` and reads
the host-injected ``window.__LEMMA_CONFIG__``). The opt-in web components
(``<lemma-agent-task>`` / ``<lemma-agent-thread>``) load alongside it from
``GET /public/sdk/lemma-ui.js`` — kept a separate request so the client bundle
stays lean for apps that don't need the components.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core.config import settings

router = APIRouter(
    prefix="/public/sdk",
    tags=["Public SDK"],
    redirect_slashes=False,
)

# (path, mtime) -> bytes. Bundles live at fixed URLs, so we cache each read and
# invalidate per-path when the file changes on disk (dev rebuilds on startup).
_bundle_cache: dict[tuple[str, float], bytes] = {}


def _load_bundle_bytes(bundle_path: Path | None, *, label: str) -> bytes:
    if bundle_path is None:
        raise HTTPException(status_code=404, detail=f"{label} not available")

    mtime = bundle_path.stat().st_mtime
    key = (str(bundle_path), mtime)
    cached = _bundle_cache.get(key)
    if cached is not None:
        return cached

    data = bundle_path.read_bytes()
    # Drop stale entries for THIS path (older mtimes); keep other bundles cached.
    for existing in [k for k in _bundle_cache if k[0] == str(bundle_path)]:
        del _bundle_cache[existing]
    _bundle_cache[key] = data
    return data


def _bundle_response(bundle_path: Path | None, *, label: str) -> Response:
    return Response(
        content=_load_bundle_bytes(bundle_path, label=label),
        media_type="application/javascript",
        # Fixed URL: no-cache (revalidate) so a rebuilt bundle is never pinned.
        headers={"Cache-Control": "public, no-cache"},
    )


@router.get(
    "/lemma-client.js",
    operation_id="public.sdk.browser_client",
    summary="Get Browser SDK Bundle",
    include_in_schema=False,
)
async def get_browser_sdk() -> Response:
    return _bundle_response(
        settings.resolve_browser_sdk_path(), label="Browser SDK bundle"
    )


@router.get(
    "/lemma-sdk.js",
    operation_id="public.sdk.browser_client_legacy",
    summary="Get Browser SDK Bundle (legacy alias)",
    include_in_schema=False,
)
async def get_browser_sdk_legacy() -> Response:
    # Back-compat: conversation widgets historically loaded /public/sdk/lemma-sdk.js.
    # It serves the same unified bundle (which also exposes window.Lemma).
    return _bundle_response(
        settings.resolve_browser_sdk_path(), label="Browser SDK bundle"
    )


@router.get(
    "/lemma-ui.js",
    operation_id="public.sdk.browser_ui",
    summary="Get Browser UI Bundle (web components)",
    include_in_schema=False,
)
async def get_browser_ui() -> Response:
    # Opt-in web components bundle for no-build HTML apps. Self-registers
    # <lemma-agent-task> / <lemma-agent-thread> on load; reuses window.LemmaClient
    # from the client bundle, so the app loads lemma-client.js first.
    return _bundle_response(
        settings.resolve_browser_ui_path(), label="Browser UI bundle"
    )
