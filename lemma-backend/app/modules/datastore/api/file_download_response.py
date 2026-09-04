"""Cache-correct HTTP responses for datastore originals and child artifacts.

Both builders receive a body that is *already* fully in memory. That matters
for how it is sent: ``StreamingResponse(BytesIO(content))`` looks like it
streams, but iterating a ``BytesIO`` yields one **line** at a time — it splits
on ``\n`` — so a binary body is emitted as one ASGI message per newline byte
it happens to contain. Measured against dev, throughput was a flat ~3,750
chunks/second regardless of chunk size, which made download time a function of
how many ``0x0A`` bytes a file contained rather than how large it was: a 2.1MB
PDF with 51,571 newlines took 13.8 seconds while a 6.4MB one with 28,778 took
7.6. Reading the same objects straight from storage took 40 milliseconds.

A body held in memory is sent as one response. It is also the more honest
answer to the client, which now gets a ``Content-Length`` instead of a chunked
transfer of unknown size.
"""

from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import quote

from fastapi import Response, status

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
    return Response(content=content, media_type=content_type, headers=cache_headers)


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
    return Response(content=content, media_type=content_type, headers=cache_headers)
