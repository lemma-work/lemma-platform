"""Reading a request body under a ceiling the route chooses for itself."""

from __future__ import annotations

from fastapi import Request

from app.core.domain.errors import PayloadTooLargeError


class BodyTooLargeError(PayloadTooLargeError):
    """``PayloadTooLargeError``, plus how much of the body had arrived.

    ``received`` is for the route's own log and never reaches the response,
    which stays the middleware's envelope. It is what was read off the
    connection -- zero when the declared length alone was enough to refuse --
    and never the declared length, which is only the sender's claim.
    """

    def __init__(self, *, max_bytes: int, received: int) -> None:
        super().__init__(max_bytes=max_bytes)
        self.received = received


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

    Raised as ``BodyTooLargeError`` so the 413 is the same envelope the
    middleware sends, with this route's ceiling in ``details.max_bytes``.
    """
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        declared = None
    if declared is not None and declared > max_bytes:
        raise BodyTooLargeError(max_bytes=max_bytes, received=0)

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise BodyTooLargeError(
                max_bytes=max_bytes, received=len(body) + len(chunk)
            )
        body.extend(chunk)
    return bytes(body)
