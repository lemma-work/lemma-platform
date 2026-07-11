"""Public (unauthenticated) datastore file serving via signed tokens.

Used as the "fake signed URL" backend when object storage is local filesystem
(obstore ``LocalStore`` can't issue real signed URLs). The HMAC token *is* the
authorization — it embeds ``(pod_id, path, expiry)`` and is validated here before
any bytes are streamed. On GCS this route is unused (clients hit the real signed
URL directly).

Mounted under ``/public/datastore`` which is auth-excluded in ``security.py``.
"""

from __future__ import annotations

import mimetypes
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.files.file_url import (
    InvalidFileUrlToken,
    verify_object_token_claims,
)
from app.modules.datastore.services.files.http_cache import (
    file_cache_headers,
    if_none_match_matches,
    quote_content_etag,
)

router = APIRouter(
    prefix="/public/datastore/files",
    tags=["Public Datastore Files"],
    redirect_slashes=False,
)


@router.get("", include_in_schema=False)
async def serve_signed_file(request: Request, token: str = Query(...)) -> Response:
    try:
        claims = verify_object_token_claims(token)
    except InvalidFileUrlToken:
        raise HTTPException(status_code=403, detail="Invalid or expired file token")

    storage = create_datastore_storage()
    key = claims.object_key
    content_sha256 = claims.content_sha256
    remaining_ttl = max(0, claims.expires_at_epoch - int(time.time()))
    cache_control = (
        f"public, max-age={remaining_ttl}, immutable"
        if content_sha256
        else "public, no-cache"
    )
    cache_headers = file_cache_headers(content_sha256, cache_control=cache_control)
    if if_none_match_matches(
        request.headers.get("if-none-match"), quote_content_etag(content_sha256)
    ):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    filename = key.rsplit("/", 1)[-1] or "file"

    # Prime the stream so a missing/unreadable object fails as a 404 *before* we
    # start a 200 response, rather than erroring mid-body.
    iterator = storage.iter_download(key).__aiter__()
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
