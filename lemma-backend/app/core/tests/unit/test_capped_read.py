"""The cap has to stop the transfer, not describe it afterwards."""

from __future__ import annotations

import pytest

from app.core.net.capped_read import ResponseTooLargeError, read_capped


async def _chunks(sizes: list[int], *, sent: list[int] | None = None):
    for size in sizes:
        if sent is not None:
            sent.append(size)
        yield b"x" * size


async def test_a_body_under_the_cap_comes_back_whole() -> None:
    body = await read_capped(_chunks([10, 10, 5]), max_bytes=100)
    assert body == b"x" * 25


async def test_the_cap_is_exact_at_the_boundary() -> None:
    assert len(await read_capped(_chunks([50, 50]), max_bytes=100)) == 100
    with pytest.raises(ResponseTooLargeError):
        await read_capped(_chunks([50, 51]), max_bytes=100)


async def test_the_transfer_stops_rather_than_finishing_and_then_complaining() -> None:
    """The point of the whole helper, stated as a test.

    The pattern this replaced read the entire body and *then* compared its
    length to the cap, which bounds nothing: by the time the check runs the
    memory is already committed, and a sender that ignores the declared size
    decides how much of the worker's heap to take.

    So the assertion is about what was pulled off the wire, not about the
    return value. A 10 MB body against a 1 KB cap must cost about a chunk.
    """
    sent: list[int] = []
    ten_mb_in_chunks = [64 * 1024] * 160

    with pytest.raises(ResponseTooLargeError) as excinfo:
        await read_capped(_chunks(ten_mb_in_chunks, sent=sent), max_bytes=1024)

    assert sum(sent) <= 64 * 1024, (
        f"pulled {sum(sent)} bytes off the wire for a 1024-byte cap; the read "
        "is running to completion before checking, which is the bug"
    )
    assert excinfo.value.max_bytes == 1024


async def test_an_empty_body_is_not_an_error() -> None:
    assert await read_capped(_chunks([]), max_bytes=10) == b""
