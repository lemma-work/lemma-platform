"""Cache-correct HTTP responses for datastore originals and child artifacts."""

from __future__ import annotations

import hashlib
import unicodedata
from io import BytesIO
from urllib.parse import quote

from fastapi import Response, status
from fastapi.responses import StreamingResponse

from app.modules.datastore.services.files.http_cache import (
    file_cache_headers,
    if_none_match_matches,
    quote_content_etag,
)


def build_content_disposition(disposition_type: str, filename: str) -> str:
    normalized_ascii = (
        unicodedata.normalize("NFKD", filename)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_filename = (
        (normalized_ascii or "download").replace("\\", "_").replace('"', "_")
    )
    encoded_filename = quote(filename, safe="")
    return (
        f'{disposition_type}; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


def build_original_download_response(file_entity, download) -> Response:
    cache_headers = file_cache_headers(
        file_entity.content_sha256,
        cache_control=(
            "private, no-cache" if file_entity.content_sha256 else "private, no-store"
        ),
    )
    cache_headers["Vary"] = "Authorization, Cookie"
    if download.not_modified:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

    content = download.content
    assert content is not None
    content_type = file_entity.content_type
    inline = content_type.startswith(("application/pdf", "image/", "text/"))
    cache_headers["Content-Disposition"] = build_content_disposition(
        "inline" if inline else "attachment", file_entity.name
    )
    return StreamingResponse(
        BytesIO(content), media_type=content_type, headers=cache_headers
    )


def build_child_download_response(
    *,
    request_if_none_match: str | None,
    artifact_name: str,
    content: bytes,
    content_type: str,
) -> Response:
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    cache_headers = file_cache_headers(
        artifact_sha256, cache_control="private, no-cache"
    )
    cache_headers["Vary"] = "Authorization, Cookie"
    if if_none_match_matches(
        request_if_none_match, quote_content_etag(artifact_sha256)
    ):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)

    inline = content_type.startswith(("text/", "image/", "application/json"))
    cache_headers["Content-Disposition"] = build_content_disposition(
        "inline" if inline else "attachment",
        artifact_name.rsplit("/", 1)[-1],
    )
    return StreamingResponse(
        BytesIO(content), media_type=content_type, headers=cache_headers
    )
