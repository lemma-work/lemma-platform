"""Public (unauthenticated) short-link serving for datastore files.

A short link ``{api_url}/s/{code}`` resolves a Redis-backed capability code,
records one hit (atomically), and streams the file bytes — but only while the
link is unexpired and under its per-link hit cap. Bytes are proxied through the
backend (never a redirect to a real object-store signed URL) so the hit cap
genuinely bounds egress.

Mounted under ``/s`` which is auth-excluded in ``security.py``.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.files.signed_url import (
    SignedUrlExhausted,
    SignedUrlNotFound,
    get_signed_url_store,
)
from app.modules.datastore.services.files.http_cache import (
    file_cache_headers,
    if_none_match_matches,
    quote_content_etag,
)

router = APIRouter(prefix="/s", tags=["Public Datastore Files"], redirect_slashes=False)


@router.get("/{code}", include_in_schema=False)
async def serve_signed_url(code: str, request: Request) -> Response:
    try:
        claims = await get_signed_url_store().consume_claims(code)
    except SignedUrlNotFound:
        raise HTTPException(status_code=404, detail="Link not found or expired")
    except SignedUrlExhausted:
        raise HTTPException(status_code=410, detail="Link hit limit reached")

    object_key = claims.object_key
    storage = create_datastore_storage()
    content_sha256 = claims.content_sha256
    cache_headers = file_cache_headers(
        content_sha256, cache_control="private, no-cache"
    )
    if if_none_match_matches(
        request.headers.get("if-none-match"), quote_content_etag(content_sha256)
    ):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
    content_type = mimetypes.guess_type(object_key)[0] or "application/octet-stream"
    filename = object_key.rsplit("/", 1)[-1] or "file"

    # Prime the stream so a missing/unreadable object fails as a 404 *before* we
    # start a 200 response, rather than erroring mid-body.
    iterator = storage.iter_download(object_key).__aiter__()
    try:
        first_chunk = await iterator.__anext__()
    except StopAsyncIteration:
        first_chunk = b""
    except DatastoreObjectNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to stream file")

    async def _stream():
        if first_chunk:
            yield first_chunk
        async for chunk in iterator:
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers={
            **cache_headers,
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )
