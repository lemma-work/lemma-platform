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

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.files.file_url import (
    InvalidFileUrlToken,
    verify_object_token,
)

router = APIRouter(
    prefix="/public/datastore/files",
    tags=["Public Datastore Files"],
    redirect_slashes=False,
)


@router.get("", include_in_schema=False)
async def serve_signed_file(token: str = Query(...)) -> StreamingResponse:
    try:
        object_key = verify_object_token(token)
    except InvalidFileUrlToken:
        raise HTTPException(status_code=403, detail="Invalid or expired file token")

    storage = create_datastore_storage()
    key = object_key
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
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
