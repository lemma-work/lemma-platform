"""Public (unauthenticated) short-link serving for datastore files.

A short link ``{api_url}/s/{code}`` resolves a Redis-backed capability code and
streams the file bytes — but only while the link is unexpired and has budget
left. Bytes are proxied through the backend (never a redirect to a real
object-store signed URL) so the budget genuinely bounds egress.

The link is live: it serves the file at its path as it is now, re-checked as the
person who shared it (``shared_link_files``). A shared page also serves what it
embeds, at ``{api_url}/s/{code}/a?ref=<reference as the page writes it>`` — the
reference rather than a path, so the reader's page never needs to know where in
the pod the page lives.

This is the one route in the product whose audience is "whoever the recipient
is, in whatever browser they happen to have", so it answers to browser rules
rather than API rules: it supports ``Range`` (without which Safari will not play
a shared video and Chrome's PDF viewer degrades), it never serves active content
inline, and it renders its errors as a page when a browser asks for one. The
headers themselves are built by ``file_stream_response``, shared with the
token-based public route.

Mounted under ``/s`` which is auth-excluded in ``security.py``.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from app.modules.datastore.api.file_stream_response import (
    build_streamed_file_response,
)
from app.modules.datastore.api.shared_link_files import (
    SharedFileUnavailable,
    embedded_file,
    live_file,
)
from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.services.files.signed_url import (
    SignedUrlEmbeddedAllowanceSpent,
    SignedUrlExhausted,
    SignedUrlNotFound,
    get_signed_url_store,
)
from app.modules.datastore.services.files.link_budget import open_units

router = APIRouter(prefix="/s", tags=["Public Datastore Files"], redirect_slashes=False)

_ERROR_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
 :root{{color-scheme:light dark}}
 body{{margin:0;min-height:100vh;display:grid;place-items:center;
   font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px}}
 main{{max-width:28rem;text-align:center}}
 h1{{font-size:1.25rem;margin:0 0 .5rem}}
 p{{margin:0;opacity:.7}}
</style></head>
<body><main><h1>{title}</h1><p>{detail}</p></main></body></html>
"""


def _wants_html(request: Request) -> bool:
    """Whether this looks like a browser navigation rather than an API call.

    `text/html` ahead of `*/*` is what a navigating browser sends and what no
    SDK sends, so it is a good enough signal — and the cost of being wrong is
    only which representation of the same error the caller gets.
    """
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def _fail(
    request: Request, *, code: int, title: str, detail: str
) -> HTTPException | Response:
    if _wants_html(request):
        return HTMLResponse(
            _ERROR_PAGE.format(title=html.escape(title), detail=html.escape(detail)),
            status_code=code,
        )
    return HTTPException(status_code=code, detail=detail)


def _raise_or_return(result: HTTPException | Response) -> Response:
    if isinstance(result, HTTPException):
        raise result
    return result


@router.get("", include_in_schema=False)
async def missing_code() -> Response:
    """`/s` with no code is a missing link, not an authentication problem.

    Without this route the request falls through to the app's global auth
    dependency, whose exclusion list holds the prefix `/s/` — and
    `TrailingSlashMiddleware` has by then rewritten `/s/` to `/s`, which no
    longer matches it. The result was a 401 for what is plainly a 404. Fixed
    here rather than by loosening the exclusion to `/s`, which would quietly
    make every future `/s*` path public.
    """
    raise HTTPException(status_code=404, detail="Link not found or expired")


@router.api_route("/{code}", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_signed_url(code: str, request: Request) -> Response:
    store = get_signed_url_store()

    # Peek first, charge later. Deciding the whole response before spending any
    # budget is what keeps a revalidation, a link-unfurler's HEAD and a missing
    # object from each costing a download.
    try:
        claims = await store.peek_claims(code)
    except SignedUrlNotFound:
        return _raise_or_return(
            _fail(
                request,
                code=404,
                title="This link has expired",
                detail="The link is no longer valid. Ask whoever shared it for a new one.",
            )
        )
    except SignedUrlExhausted:
        return _raise_or_return(
            _fail(
                request,
                code=410,
                title="This link has reached its download limit",
                detail="The file has been downloaded too many times. Ask whoever shared it for a new link.",
            )
        )

    file_gone = _fail(
        request,
        code=404,
        title="File not found",
        detail="The file behind this link no longer exists.",
    )
    try:
        current = await live_file(code, claims, claims.path)
        response, billable_bytes = await build_streamed_file_response(
            request,
            create_datastore_storage(),
            object_key=current.object_key,
            content_type=current.content_type,
            filename=current.filename,
            # The digest of what is there now. The minted one made a browser
            # holding the old copy revalidate to a 304 and never see an edit.
            content_sha256=current.content_sha256,
            # `no-cache` is revalidate-every-time, not don't-store: the ETag
            # still saves the transfer, and now saves the budget with it.
            cache_control="private, no-cache",
        )
    except SharedFileUnavailable, DatastoreObjectNotFoundError:
        return _raise_or_return(file_gone)

    units = open_units(billable_bytes, current.size_bytes)
    if units > 0:
        try:
            await store.consume_claims(code, units_wanted=units)
        except SignedUrlExhausted:
            return _raise_or_return(
                _fail(
                    request,
                    code=410,
                    title="This link has reached its download limit",
                    detail="The file has been downloaded too many times. Ask whoever shared it for a new link.",
                )
            )
        except SignedUrlNotFound:
            # Expired in the window between the peek and the charge.
            return _raise_or_return(
                _fail(
                    request,
                    code=404,
                    title="This link has expired",
                    detail="The link is no longer valid. Ask whoever shared it for a new one.",
                )
            )

    return response


@router.api_route("/{code}/a", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_embedded_resource(
    code: str,
    request: Request,
    ref: str = Query(..., max_length=2048),
) -> Response:
    """Something a shared page embeds — a picture, a stylesheet, a script.

    Plain status codes, never the HTML error page: what asks for this is an
    `<img>` or a `<link>`, not a person. And one 404 for every refusal, so a
    reader cannot use a link to learn which files exist.
    """
    store = get_signed_url_store()
    not_here = HTTPException(status_code=404, detail="Not part of this page")
    try:
        claims = await store.peek_claims(code)
    except SignedUrlNotFound:
        raise not_here from None
    except SignedUrlExhausted:
        raise HTTPException(
            status_code=410, detail="This link has been used up"
        ) from None

    storage = create_datastore_storage()
    try:
        resource = await embedded_file(code, claims, ref, storage)
        response, billable_bytes = await build_streamed_file_response(
            request,
            storage,
            object_key=resource.object_key,
            content_type=resource.content_type,
            filename=resource.filename,
            content_sha256=resource.content_sha256,
            cache_control="private, no-cache",
        )
    except SharedFileUnavailable, DatastoreObjectNotFoundError:
        raise not_here from None

    if billable_bytes > 0:
        try:
            await store.consume_claims(code, embedded_bytes_wanted=billable_bytes)
        except SignedUrlNotFound:
            raise not_here from None
        except SignedUrlExhausted, SignedUrlEmbeddedAllowanceSpent:
            raise HTTPException(
                status_code=410, detail="This link has been used up"
            ) from None
    return response
