"""Public (unauthenticated) datastore file serving via signed tokens.

Used as the "fake signed URL" backend when object storage is local filesystem
(obstore ``LocalStore`` can't issue real signed URLs). The HMAC token *is* the
authorization — it embeds ``(pod_id, path, expiry)`` and is validated here before
any bytes are streamed. On GCS this route is unused (clients hit the real signed
URL directly).

The response itself is built by ``file_stream_response``, shared with the
short-link route: both are public, both stream from storage, and both used to
carry their own copy of the same four header bugs.

Mounted under ``/public/datastore`` which is auth-excluded in ``security.py``.
"""

from __future__ import annotations

import mimetypes
import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.modules.datastore.api.file_stream_response import (
    build_streamed_file_response,
)
from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.files.file_url import (
    InvalidFileUrlToken,
    verify_object_token_claims,
)

router = APIRouter(
    prefix="/public/datastore/files",
    tags=["Public Datastore Files"],
    redirect_slashes=False,
)


@router.api_route("", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_signed_file(request: Request, token: str = Query(...)) -> Response:
    try:
        claims = verify_object_token_claims(token)
    except InvalidFileUrlToken:
        raise HTTPException(status_code=403, detail="Invalid or expired file token")

    key = claims.object_key
    content_sha256 = claims.content_sha256
    remaining_ttl = max(0, claims.expires_at_epoch - int(time.time()))
    cache_control = (
        f"public, max-age={remaining_ttl}, immutable"
        if content_sha256
        else "public, no-cache"
    )

    # Unlike the short link, this token carries no recorded type or name — it is
    # minted from an object key alone — so the key's extension is all there is
    # to go on.
    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"

    try:
        response, _billable = await build_streamed_file_response(
            request,
            create_datastore_storage(),
            object_key=key,
            content_type=content_type,
            filename=key.rsplit("/", 1)[-1] or "file",
            content_sha256=content_sha256,
            cache_control=cache_control,
        )
    except DatastoreObjectNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    return response
