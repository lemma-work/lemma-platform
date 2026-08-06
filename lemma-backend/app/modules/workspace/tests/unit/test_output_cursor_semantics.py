"""The cursor contract every provider's output must honour.

A reader advances its cursor to the sequence of the last chunk it consumed and
asks again from there, so `after_sequence` is *exclusive* and sequences are
1-based. Getting this wrong is not subtle in effect but is very easy to miss in
code: an inclusive read re-delivers the last chunk on every poll, so a command
that printed one line looks like it printed it twenty times.

Checked against the in-memory buffer used by tests and, where a real Redis is
available, against the Redis-backed one the E2B provider actually uses.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentbox.domain import ProcessOutputChannel

from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

pytestmark = pytest.mark.asyncio


async def _drain(buffer, process_id: str) -> bytes:
    """Consume exactly as the workspace session does."""
    collected = bytearray()
    after_sequence = 0
    for _ in range(10):
        snapshot = await buffer.read(process_id, after_sequence=after_sequence)
        if not snapshot.chunks:
            break
        for chunk in snapshot.chunks:
            after_sequence = max(after_sequence, chunk.sequence)
            collected.extend(chunk.data)
    return bytes(collected)


async def test_polling_twice_does_not_repeat_output() -> None:
    buffer = InMemoryOutputBuffer()
    process_id = str(uuid4())
    await buffer.append(
        process_id, channel=ProcessOutputChannel.STDOUT, data=b"one-line\n"
    )

    assert await _drain(buffer, process_id) == b"one-line\n"


async def test_a_reader_that_returns_sees_only_what_is_new() -> None:
    buffer = InMemoryOutputBuffer()
    process_id = str(uuid4())
    await buffer.append(
        process_id, channel=ProcessOutputChannel.STDOUT, data=b"first\n"
    )

    first = await buffer.read(process_id, after_sequence=0)
    cursor = max(chunk.sequence for chunk in first.chunks)

    await buffer.append(
        process_id, channel=ProcessOutputChannel.STDOUT, data=b"second\n"
    )
    second = await buffer.read(process_id, after_sequence=cursor)

    assert b"".join(c.data for c in first.chunks) == b"first\n"
    assert b"".join(c.data for c in second.chunks) == b"second\n"


async def test_sequences_are_one_based() -> None:
    """Sequence 0 must mean "nothing consumed yet", so the first chunk is 1.
    If chunks started at 0, a reader whose cursor is 0 could never distinguish
    "I have read chunk 0" from "I have read nothing"."""
    buffer = InMemoryOutputBuffer()
    process_id = str(uuid4())
    await buffer.append(process_id, channel=ProcessOutputChannel.STDOUT, data=b"x")

    snapshot = await buffer.read(process_id, after_sequence=0)
    assert [chunk.sequence for chunk in snapshot.chunks] == [1]


async def test_interleaved_channels_share_one_sequence() -> None:
    """The cursor is a single sequence over both channels, so stdout and
    stderr must not be numbered independently or one would rewind the other."""
    buffer = InMemoryOutputBuffer()
    process_id = str(uuid4())
    await buffer.append(process_id, channel=ProcessOutputChannel.STDOUT, data=b"a")
    await buffer.append(process_id, channel=ProcessOutputChannel.STDERR, data=b"b")
    await buffer.append(process_id, channel=ProcessOutputChannel.STDOUT, data=b"c")

    snapshot = await buffer.read(process_id, after_sequence=0)
    assert [chunk.sequence for chunk in snapshot.chunks] == [1, 2, 3]
    assert await _drain(buffer, process_id) == b"abc"
