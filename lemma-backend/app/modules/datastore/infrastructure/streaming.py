"""Helpers for moving document bytes through temp files without holding the
whole file (plus copies) in memory during extraction.

The datastore worker used to ``download_file`` the entire document into ``bytes``
and then wrap it in a ``BytesIO`` for the Kreuzberg multipart upload — two full
copies resident at once. Streaming the download to a temp file and streaming that
file back up keeps peak memory at ~one chunk instead of ~2× the file size, which
matters for large documents on a memory-constrained worker.

These are plain sync file ops meant to be dispatched via
``app.core.concurrency.offload.run_blocking`` (or awaited chunk-by-chunk from an
async stream), never called directly on the event loop with large data.
"""

from __future__ import annotations

import os
import tempfile
from typing import AsyncIterator


def read_file_bytes(path: str) -> bytes:
    """Read a file fully into bytes (for the in-process markitdown/docling path)."""
    with open(path, "rb") as handle:
        return handle.read()


def open_binary(path: str):
    """Open a file for streaming upload. Kept as a helper so callers dispatch it
    via run_blocking rather than calling open() on the event loop."""
    return open(path, "rb")


async def stream_to_tempfile(
    chunks: AsyncIterator[bytes], *, suffix: str = ""
) -> str:
    """Write an async byte-chunk stream to a NamedTemporaryFile; return its path.

    Individual ``write`` calls are buffered syscalls (fast, GIL-releasing); the
    bytes are never all held in memory. The caller owns cleanup (``os.unlink``).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        async for chunk in chunks:
            tmp.write(chunk)
        tmp.flush()
        tmp.close()
        return tmp.name
    except BaseException:
        # Don't leak the temp file if the download stream fails partway.
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
