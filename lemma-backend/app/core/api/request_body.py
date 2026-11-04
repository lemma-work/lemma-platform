"""Reading a request body under a ceiling the route chooses for itself."""

from __future__ import annotations

from fastapi import Request

from app.core.domain.errors import PayloadTooLargeError


async def read_body_limited(request: Request, *, max_bytes: int) -> bytes:
    """The request body, refused with a 413 the moment it passes ``max_bytes``.

    ``RequestBodyLimitMiddleware`` bounds every request, but at a size chosen
    for uploads. A webhook route reads its body before it can check the
    signature over it, so on an unauthenticated route that upload-sized ceiling
    is what any sender gets to make this process buffer. This is the route's
    own ceiling, applied as the bytes arrive rather than after they all have.

    A declared ``Content-Length`` over the ceiling is refused before any of the
    body is read. It is only a hint -- a sender can declare less than it sends,
    or send chunked with no length at all -- so the running total is what is
    enforced. A length that does not parse costs the early refusal and nothing
    else, as in the middleware; under uvicorn one never arrives, because the
    HTTP parser rejects it before the app is called.

    Raised as ``PayloadTooLargeError`` so the 413 is the same envelope the
    middleware sends, with this route's ceiling in ``details.max_bytes``.
    """
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        declared = None
    if declared is not None and declared > max_bytes:
        raise PayloadTooLargeError(max_bytes=max_bytes)

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise PayloadTooLargeError(max_bytes=max_bytes)
        body.extend(chunk)
    return bytes(body)
