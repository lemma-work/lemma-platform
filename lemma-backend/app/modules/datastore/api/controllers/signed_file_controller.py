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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.files.signed_url import (
    SignedUrlExhausted,
    SignedUrlNotFound,
    get_signed_url_store,
)

router = APIRouter(prefix="/s", tags=["Public Datastore Files"], redirect_slashes=False)


@router.get("/{code}", include_in_schema=False)
async def serve_signed_url(code: str) -> StreamingResponse:
    try:
        object_key = await get_signed_url_store().consume(code)
    except SignedUrlNotFound:
        raise HTTPException(status_code=404, detail="Link not found or expired")
    except SignedUrlExhausted:
        raise HTTPException(status_code=410, detail="Link hit limit reached")

    storage = create_datastore_storage()
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
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
