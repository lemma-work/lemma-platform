"""Collect a streamed HTTP body without letting the sender choose the size.

The pattern this replaces was everywhere on the inbound-attachment paths::

    content = response.content          # or: await response.read()
    if len(content) > CAP:
        return None

which checks the cap *after* the whole body is already resident. The declared
size a platform sends is a hint, not a promise -- several never send one -- so
the effective limit was whatever the remote decided to transmit, into a worker
that shares its heap with every other job on the replica.

Streaming with a running total gives up mid-transfer instead, so the peak
memory is a chunk rather than the payload. This is not SSRF protection and does
not try to be: these are authenticated calls to known platform APIs, so there is
no redirect to re-validate and no address to check. Use ``fetch_guarded`` in
``url_guard`` for URLs a tenant supplies. This is only about the size.
"""

from __future__ import annotations

from collections.abc import AsyncIterator


class ResponseTooLargeError(Exception):
    """A response body exceeded the caller's cap and was abandoned."""

    def __init__(self, max_bytes: int, read_bytes: int) -> None:
        super().__init__(
            f"Response exceeded the {max_bytes} byte cap "
            f"(gave up after {read_bytes} bytes)"
        )
        self.max_bytes = max_bytes
        self.read_bytes = read_bytes


async def read_capped(chunks: AsyncIterator[bytes], *, max_bytes: int) -> bytes:
    """Join a chunked body, raising as soon as it passes ``max_bytes``.

    Takes an iterator rather than a response so it serves both clients in the
    tree: ``httpx`` gives one from ``response.aiter_bytes()``, ``aiohttp`` from
    ``response.content.iter_chunked(n)``.

    Raising rather than truncating is deliberate. A truncated attachment is a
    corrupt one, and it would be indistinguishable downstream from a small file
    that happens to be complete -- the caller needs to know it got nothing
    usable, not a prefix.
    """
    collected: list[bytes] = []
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(max_bytes, total)
        collected.append(chunk)
    return b"".join(collected)
