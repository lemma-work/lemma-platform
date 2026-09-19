"""Reading part of a file out of an E2B sandbox, without reading all of it.

`sandbox.files.read` has no notion of a range: it returns the whole file and
the caller slices. That is fine for a thumbnail and ruinous for the thing it
is actually asked for now. The workspace file API caps one response at 8 MiB
and the clients read a larger file as a series of ranges -- so a 1 GiB
download became 128 requests, each of which pulled the whole gigabyte. 128
GiB over the wire, and a gigabyte resident in the API process every time.

envd serves files over HTTP and honours `Range`. Measured against a real
sandbox: `Range: bytes=1048576-2097151` on a 20 MiB file answers `206` with
`Content-Range: bytes 1048576-2097151/20971520`, `Accept-Ranges: bytes` and
exactly 1048576 bytes of body. So the range can be asked for rather than
applied afterwards, and the same 1 GiB download moves 1 GiB.

The SDK is still what builds the URL, because a secured sandbox needs its
signature and `download_url` is what knows how to make one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import httpx

from app.modules.workspace.providers.e2b_common import classify_path, sdk_errors
from app.modules.workspace.providers.e2b_process_lifetime import seconds_until
from sandbox_runtime.errors import SandboxPathNotFound
from sandbox_runtime.protocol import ByteRange

#: What to hand the caller at a time. Big enough that a large file is not a
#: million awaits, small enough that nothing here holds a meaningful amount
#: of the file at once -- which is the whole point.
CHUNK_BYTES = 256 * 1024

#: The floor on a read's deadline. A caller whose deadline has already passed
#: still gets one honest attempt rather than a timeout configured to zero,
#: which httpx treats as "fail immediately" rather than "no limit".
MIN_TIMEOUT_SECONDS = 5.0


def _header(byte_range: ByteRange) -> dict[str, str]:
    """`byte_range` as a `Range` header, or nothing for a whole file."""
    offset = byte_range.offset or 0
    if byte_range.length is None:
        if offset == 0:
            return {}
        return {"Range": f"bytes={offset}-"}
    if byte_range.length == 0:
        # An explicit request for nothing. Asking the server for
        # `bytes=n-(n-1)` would be malformed, so this is caught by the
        # caller before a request happens.
        return {}
    return {"Range": f"bytes={offset}-{offset + byte_range.length - 1}"}


async def read_range(
    sandbox,
    *,
    path: str,
    byte_range: ByteRange,
    deadline_at: datetime,
) -> AsyncIterator[bytes]:
    """Stream the requested bytes of `path`, and no more than that.

    A server that honours the `Range` sends only those bytes and this passes
    them straight through. One that ignores it and sends the whole file --
    answering `200` where a `206` was asked for -- is handled by skipping and
    stopping early, so the worst case is the old behaviour rather than a
    silently wrong slice. Nothing here assumes the 206 it measured.
    """
    offset = byte_range.offset or 0
    if byte_range.length == 0:
        return

    with sdk_errors(path):
        url = sandbox.download_url(path)
    timeout = max(seconds_until(deadline_at), MIN_TIMEOUT_SECONDS)
    headers = _header(byte_range)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416:
                    # The range starts past the end of the file. An empty
                    # read is the honest answer, and the layer above turns it
                    # into the 416 a caller sees.
                    return
                if response.status_code == 404:
                    raise SandboxPathNotFound(path)
                response.raise_for_status()
                served_range = response.status_code == 206
                # How many bytes of the stream to throw away before the
                # wanted ones start, and how many to keep. Both are zero-work
                # on a 206, which is the path that actually runs.
                skip = 0 if served_range else offset
                remaining = byte_range.length
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    if skip:
                        if len(chunk) <= skip:
                            skip -= len(chunk)
                            continue
                        chunk = chunk[skip:]
                        skip = 0
                    if remaining is not None:
                        if len(chunk) >= remaining:
                            yield chunk[:remaining]
                            return
                        remaining -= len(chunk)
                    yield chunk
    except (httpx.HTTPError, OSError) as exc:
        # Same treatment the SDK calls get: the transport's own exception
        # types are not this module's vocabulary, and a caller cannot act on
        # `httpx.ReadTimeout`. Named rather than caught broadly -- a
        # `NameError` in the loop above is this module's bug and must not be
        # reported as a sandbox that could not be reached.
        raise classify_path(exc, path) from exc


__all__ = ["CHUNK_BYTES", "read_range"]
