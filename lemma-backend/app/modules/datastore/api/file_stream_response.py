"""Streamed HTTP responses for datastore objects served on a public route.

The sibling module ``file_download_response.py`` builds responses for bodies
that are *already in memory* and explains there why that is the right shape for
an authenticated download. This one is for the two public routes — ``/s/{code}``
and ``/public/datastore/files?token=`` — which stream straight from object
storage and must therefore decide their headers before the first byte goes out.

Both routes previously carried their own copy of this logic, and every defect
below existed twice:

- the filename went into ``Content-Disposition`` unescaped, so a name containing
  a quote broke out of the header value and a non-ASCII name raised
  ``UnicodeEncodeError`` in Starlette's latin-1 header encoding — a 500 for a
  file that was merely called ``报告.pdf``;
- the content type was re-guessed from the object key rather than taken from the
  record that already knew it, so an extensionless file downloaded as
  ``application/octet-stream`` instead of rendering;
- everything was served ``inline`` on the API origin — the origin that holds the
  session cookies — so an uploaded ``.html`` or ``.svg`` executed script there.
  There is no CSP middleware in this backend; ``mod:icon`` hardened exactly this
  and ``/s/`` never got the same treatment;
- there was no ``Content-Length`` and no ``Range`` support, so Safari would not
  play a shared video at all and Chrome's PDF viewer had to fall back.
"""

from __future__ import annotations

from fastapi import Request, Response, status
from fastapi.responses import StreamingResponse

from app.modules.datastore.api.file_download_response import build_content_disposition
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.services.files.http_cache import (
    file_cache_headers,
    if_none_match_matches,
    quote_content_etag,
)

# Types a browser may render in place without running anything. Deliberately a
# prefix allowlist with one hole punched in it: `image/svg+xml` is an image that
# executes script, so it is the one image type that must never be inline.
# Everything unlisted — HTML above all — is handed over as a download.
_INLINE_MEDIA_PREFIXES = ("application/pdf", "image/", "audio/", "video/")
_INLINE_MEDIA_EXACT = ("text/plain",)
_NEVER_INLINE = ("image/svg+xml",)

#: Sentinel for a syntactically valid ``Range`` that no part of the object can
#: satisfy. Distinct from ``None`` (no range asked for, or one we chose to
#: ignore), because the two have different answers: 416 versus a plain 200.
UNSATISFIABLE = "unsatisfiable"


def is_inline_media_type(content_type: str) -> bool:
    """Whether this type is safe to render in the browser rather than download."""
    base = content_type.split(";", 1)[0].strip().lower()
    if base in _NEVER_INLINE:
        return False
    return base in _INLINE_MEDIA_EXACT or base.startswith(_INLINE_MEDIA_PREFIXES)


def parse_byte_range(
    range_header: str | None, size: int
) -> tuple[int, int] | str | None:
    """Resolve a ``Range`` header against a known object size.

    Returns a half-open ``(start, end)``, ``UNSATISFIABLE``, or ``None`` when
    there is nothing to honour. Anything malformed returns ``None`` rather than
    416: RFC 9110 says an unparsable Range must be ignored, and answering 416 to
    a header we simply did not understand would break the request for no reason.

    Only a single range is honoured. Multi-range requires a multipart body and
    no browser needs it for the media playback and PDF paging this exists for.
    """
    if not range_header or size <= 0:
        return None
    units, _, spec = range_header.partition("=")
    if units.strip().lower() != "bytes":
        return None
    spec = spec.strip()
    if "," in spec:  # multi-range: ignore, serve the whole object
        return None
    first, sep, last = spec.partition("-")
    if not sep:
        return None

    try:
        if not first:
            # Suffix form, `bytes=-500`: the final N bytes.
            suffix = int(last)
            if suffix <= 0:
                return UNSATISFIABLE
            return max(0, size - suffix), size
        start = int(first)
        end = int(last) + 1 if last else size
    except ValueError:
        return None

    if start < 0 or start >= size:
        return UNSATISFIABLE
    if end <= start:
        # `bytes=500-400`. Left alone this returns a half-open range that runs
        # backwards, which becomes a negative Content-Length and a storage read
        # nobody can satisfy.
        return UNSATISFIABLE
    return start, min(end, size)


def _body_headers(
    *,
    content_type: str,
    filename: str,
    length: int,
    cache_headers: dict[str, str],
) -> dict[str, str]:
    return {
        **cache_headers,
        "Content-Disposition": build_content_disposition(
            "inline" if is_inline_media_type(content_type) else "attachment",
            filename,
        ),
        # Belt and braces with the disposition above: without it a browser may
        # sniff an `application/octet-stream` body back into HTML and run it.
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }


async def build_streamed_file_response(
    request: Request,
    storage: DatastoreStoragePort,
    *,
    object_key: str,
    content_type: str,
    filename: str,
    content_sha256: str | None,
    cache_control: str,
) -> tuple[Response, int]:
    """Build the response for a public file fetch.

    Returns ``(response, billable_bytes)`` — the second element is how many bytes
    this response commits to sending, which is what a caller metering egress
    should charge. It is zero for a 304, a HEAD and a 416, none of which transfer
    a body.

    Raises ``DatastoreObjectNotFoundError`` when the object is gone, so the
    caller decides what a missing object looks like on its own route.
    """
    cache_headers = file_cache_headers(content_sha256, cache_control=cache_control)

    if if_none_match_matches(
        request.headers.get("if-none-match"), quote_content_etag(content_sha256)
    ):
        return (
            Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers),
            0,
        )

    range_header = request.headers.get("range")
    is_head = request.method == "HEAD"

    # A HEAD or a range needs the size before there is any response to build:
    # HEAD reports it, and obstore raises rather than clamping when a range
    # starts past the end. Read live rather than from anything recorded at mint
    # time — an object can be replaced at the same key while a link is live, and
    # a stale length would turn a satisfiable range into a 500. A plain GET,
    # which is nearly all of them, still costs no extra round trip.
    size: int | None = None
    if range_header or is_head:
        size = await storage.stat_file(object_key)

    byte_range: tuple[int, int] | None = None
    if size is not None:
        resolved = parse_byte_range(range_header, size)
        if resolved == UNSATISFIABLE:
            return (
                Response(
                    status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                    headers={**cache_headers, "Content-Range": f"bytes */{size}"},
                ),
                0,
            )
        byte_range = resolved if isinstance(resolved, tuple) else None

        if is_head:
            return (
                Response(
                    status_code=status.HTTP_200_OK,
                    media_type=content_type,
                    headers=_body_headers(
                        content_type=content_type,
                        filename=filename,
                        length=size,
                        cache_headers=cache_headers,
                    ),
                ),
                0,
            )

    total_size, chunks = await storage.open_download(object_key, byte_range=byte_range)
    length = total_size if byte_range is None else byte_range[1] - byte_range[0]
    headers = _body_headers(
        content_type=content_type,
        filename=filename,
        length=length,
        cache_headers=cache_headers,
    )
    status_code = status.HTTP_200_OK
    if byte_range is not None:
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end - 1}/{total_size}"
        status_code = status.HTTP_206_PARTIAL_CONTENT

    return (
        StreamingResponse(
            chunks,
            status_code=status_code,
            media_type=content_type,
            headers=headers,
        ),
        length,
    )
