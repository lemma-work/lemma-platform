"""Shared HTTP response builders for served HTML assets (apps and widgets).

Both the app and widget serving paths return an HTML document with pod context
injected, plus the same ETag / cache-control conventions for entrypoints vs
content-hashed static assets. These builders are the single place that logic
lives so the controllers only do routing, auth, and data-fetch.
"""

from __future__ import annotations

from uuid import UUID

from fastapi.responses import Response

from app.core.runtime_config import inject_runtime_config


def build_asset_response(
    *,
    content: bytes | str | None,
    media_type: str,
    etag: str | None,
    is_entrypoint: bool,
    not_modified: bool = False,
) -> Response:
    """Build a cache-correct response for a served asset.

    - Entrypoints (``index.html``) carry injected pod context, so they are
      ``no-cache`` (always revalidate; the ETag still enables 304).
    - Other assets are content-hashed by the bundler, so they are immutable.
    """
    cache_control = (
        "public, no-cache" if is_entrypoint else "public, max-age=31536000, immutable"
    )
    headers = {"Cache-Control": cache_control}
    if etag:
        headers["ETag"] = etag

    if not_modified:
        return Response(status_code=304, headers=headers)

    assert content is not None
    return Response(content=content, media_type=media_type, headers=headers)


def build_injected_html_response(
    document: bytes | str,
    pod_id: UUID | str,
    *,
    cache_control: str = "no-store",
) -> Response:
    """Inject ``__LEMMA_CONFIG__`` into an HTML document and return it.

    Used for documents served fresh on every request (e.g. conversation
    widgets), which default to ``no-store`` since the content can be re-authored
    within a conversation.
    """
    body = inject_runtime_config(document, pod_id)
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": cache_control},
    )
