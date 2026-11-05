"""A route's own body ceiling, enforced while the body arrives.

The route-level proofs -- that each unauthenticated webhook route answers 413
for an oversized declared length and for an oversized chunked body -- live with
those routes. These pin the reader itself: what it reads before refusing, and
what it returns when it does not.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.api.request_body import BodyTooLargeError, read_body_limited
from app.core.domain.errors import PayloadTooLargeError


def _streaming_request(
    chunks: list[bytes], *, headers: list[tuple[bytes, bytes]] | None = None
) -> tuple[Request, list[bytes]]:
    """A request whose body arrives in ``chunks``, and the chunks handed over.

    The second value is how much of the body the reader asked for, so "refused
    before reading" and "stopped at the chunk that crossed" are assertions
    about a list rather than about timing.
    """
    delivered: list[bytes] = []

    async def receive():
        if len(delivered) == len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[len(delivered)]
        delivered.append(chunk)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": len(delivered) < len(chunks),
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/example",
            "headers": headers or [],
        },
        receive,
    )
    return request, delivered


async def test_a_declared_length_over_the_ceiling_is_refused_before_any_read():
    request, delivered = _streaming_request(
        [b"x" * 9], headers=[(b"content-length", b"9")]
    )

    with pytest.raises(BodyTooLargeError) as refused:
        await read_body_limited(request, max_bytes=8)

    assert delivered == []
    assert refused.value.status_code == 413
    assert refused.value.code == "UPLOAD_TOO_LARGE"
    assert refused.value.details == {"field": "request", "max_bytes": 8}
    # The declared 9 is the sender's claim; none of it arrived.
    assert refused.value.received == 0


async def test_a_chunked_body_with_no_length_stops_at_the_chunk_that_crosses():
    request, delivered = _streaming_request([b"x" * 8, b"y", b"never read"])

    with pytest.raises(BodyTooLargeError) as refused:
        await read_body_limited(request, max_bytes=8)

    assert delivered == [b"x" * 8, b"y"]
    assert refused.value.received == 9


async def test_an_underdeclared_length_is_not_trusted():
    request, delivered = _streaming_request(
        [b"x" * 8, b"y", b"never read"], headers=[(b"content-length", b"1")]
    )

    with pytest.raises(PayloadTooLargeError):
        await read_body_limited(request, max_bytes=8)

    assert delivered == [b"x" * 8, b"y"]


@pytest.mark.parametrize(
    "declared",
    [b"not-a-length", b"9" * 5000],
    ids=["unparseable", "past-int-conversion-limit"],
)
async def test_a_length_that_does_not_parse_still_has_its_body_counted(declared):
    request, _ = _streaming_request(
        [b"x" * 8, b"y"], headers=[(b"content-length", declared)]
    )

    with pytest.raises(PayloadTooLargeError):
        await read_body_limited(request, max_bytes=8)


async def test_a_body_at_the_ceiling_is_returned_byte_for_byte():
    """The exact bytes, in order: a signature is computed over them."""
    request, _ = _streaming_request(
        [b"\x00\xff", b"ab", b"", b"\r\ncd"], headers=[(b"content-length", b"8")]
    )

    assert await read_body_limited(request, max_bytes=8) == b"\x00\xffab\r\ncd"
