"""Stream a Kreuzberg response body to a spooled temp file and parse it off-loop.

An extraction response carries the whole document's text and, when figures are
requested, base64 images inline -- tens of megabytes of JSON. Reading it whole
held the raw body as one bytes object alongside the parse. Here it is streamed
into a spooled temp file (bounded by ``kreuzberg_max_response_bytes``) and
parsed from there on a worker thread.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from typing import IO, TypeVar

import aiohttp

from app.core.concurrency.offload import run_blocking
from app.modules.datastore.config import datastore_settings

T = TypeVar("T")

# Response bodies stay in memory up to this size, then spill to a temp file.
_SPOOL_MEMORY_BYTES = 1024 * 1024
_SPOOL_READ_BYTES = 1024 * 1024


async def spool_response(response: aiohttp.ClientResponse) -> IO[bytes]:
    """Stream a response body into a spooled temp file, enforcing a byte cap.

    Small bodies stay in memory; anything past ``_SPOOL_MEMORY_BYTES`` rolls
    to disk. Writes are batched and offloaded so a disk-backed spool never
    blocks the loop. The returned file is rewound; the caller closes it.
    """
    cap = datastore_settings.kreuzberg_max_response_bytes
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES)
    complete = False
    try:
        total = 0
        pending = bytearray()
        async for chunk in response.content.iter_chunked(_SPOOL_READ_BYTES):
            total += len(chunk)
            if cap and total > cap:
                raise RuntimeError(
                    f"Kreuzberg response exceeded {cap} bytes; refusing to buffer it"
                )
            pending += chunk
            if len(pending) >= _SPOOL_READ_BYTES:
                await run_blocking(spool.write, bytes(pending))
                pending.clear()
        if pending:
            await run_blocking(spool.write, bytes(pending))
        await run_blocking(spool.seek, 0)
        complete = True
    finally:
        # Any failure -- the cap, a dropped connection, cancellation -- closes
        # the spool here; on success it is handed to the caller.
        if not complete:
            spool.close()
    return spool


async def parse_spooled(body: IO[bytes], parse: Callable[[IO[bytes]], T]) -> T:
    """Run ``parse`` on a spooled body off the event loop, then close it."""
    try:
        return await run_blocking(parse, body, limiter="cpu_bound")
    finally:
        body.close()
