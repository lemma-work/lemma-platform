"""Reading a process's output in pages loses nothing.

The workspace runtime numbers its chunks from 1 and reports the number its
next chunk will get; callers pass `next_sequence` back as `after_sequence`,
which reads strictly after it. Before the client translated the header, each
poll skipped the chunk written next -- found by a terminal that echoed a
command and never showed its answer.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeClient

pytestmark = pytest.mark.unit


class _Runtime:
    """The runtime's output endpoint over a growing list of chunks."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        after = int(request.url.params["after_seq"])
        body = b"".join(
            struct.pack("!QBI", sequence, 3, len(data)) + data
            for sequence, data in enumerate(self.chunks, start=1)
            if sequence > after
        )
        return httpx.Response(
            200,
            content=body,
            headers={
                "X-Lemma-Next-Sequence": str(len(self.chunks) + 1),
                "X-Lemma-Process-State": "running",
            },
        )


async def test_every_chunk_is_read_once_across_polls() -> None:
    runtime = _Runtime()
    client = WorkspaceRuntimeClient("http://runtime", "token")
    client._client = httpx.AsyncClient(
        base_url="http://runtime", transport=httpx.MockTransport(runtime.handle)
    )
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    read: list[bytes] = []
    after = 0
    try:
        for written in (b"$ ", b"echo hi\r\n", b"hi\r\n", b"$ "):
            runtime.chunks.append(written)
            snapshot = await client.read_output(
                "op", after_sequence=after, wait_seconds=0, deadline_at=deadline
            )
            read.extend(chunk.data for chunk in snapshot.chunks)
            after = snapshot.next_sequence

        idle = await client.read_output(
            "op", after_sequence=after, wait_seconds=0, deadline_at=deadline
        )
    finally:
        await client.close()

    assert read == [b"$ ", b"echo hi\r\n", b"hi\r\n", b"$ "]
    assert idle.chunks == () and idle.next_sequence == after == 4
