"""The E2B output buffer's cursor, at and past the retention cap.

`E2BOutputBuffer` had no unit coverage at all -- it was exercised only by an
integration test that needs a real E2B sandbox, so the defect these tests pin
survived in a file whose own docstring describes the correct behaviour.

Run against `fakeredis` rather than a hand-written stand-in on purpose. The
arithmetic here depends on exactly what `LTRIM`, `LRANGE` and `LINDEX` do at
their boundaries, and a stand-in asserts only what its author already believed
those commands do -- the same belief that produced the defect.
"""

from __future__ import annotations

import pytest
from fakeredis import aioredis as fake_aioredis

from sandbox_runtime.protocol import ProcessOutputChannel, ProcessState

from app.modules.workspace.providers import e2b_output
from app.modules.workspace.providers.e2b_output import _MAX_CHUNKS, E2BOutputBuffer


@pytest.fixture
def buffer(monkeypatch) -> E2BOutputBuffer:
    fake = fake_aioredis.FakeRedis()
    monkeypatch.setattr(e2b_output, "get_redis", lambda **_kwargs: fake)
    return E2BOutputBuffer()


async def _emit(
    buffer: E2BOutputBuffer, process_id: str, count: int, *, first: int = 1
):
    for index in range(first, first + count):
        await buffer.append(
            process_id,
            channel=ProcessOutputChannel.STDOUT,
            data=f"line-{index}\n".encode(),
        )


@pytest.mark.asyncio
async def test_a_reader_keeps_receiving_output_past_the_retention_cap(buffer) -> None:
    """The defect that made long renders look hung.

    The cursor was a list index and the list is capped, so `start_index < total`
    became permanently false once a reader had consumed `_MAX_CHUNKS` chunks:
    every later poll returned nothing, for the rest of the process's life. The
    command was still running and still producing output; the agent saw silence
    and no terminal state, which is indistinguishable from a wedged sandbox.
    """
    await _emit(buffer, "p", _MAX_CHUNKS)

    drained = await buffer.read("p", after_sequence=0)
    assert len(drained.chunks) == _MAX_CHUNKS
    assert drained.next_sequence == _MAX_CHUNKS

    # The command keeps going, as a long render does.
    await _emit(buffer, "p", 10, first=_MAX_CHUNKS + 1)
    following = await buffer.read("p", after_sequence=drained.next_sequence)

    assert [chunk.data for chunk in following.chunks] == [
        f"line-{index}\n".encode() for index in range(_MAX_CHUNKS + 1, _MAX_CHUNKS + 11)
    ]
    assert following.next_sequence == _MAX_CHUNKS + 10


@pytest.mark.asyncio
async def test_sequences_stay_absolute_after_trimming(buffer) -> None:
    """A trimmed chunk shifts every surviving list index left by one.

    While the sequence was derived from position, that shift silently
    renumbered the stream: a reader resuming at its last cursor skipped exactly
    as many chunks as had been dropped, and never learned it had.
    """
    await _emit(buffer, "p", _MAX_CHUNKS + 500)

    snapshot = await buffer.read("p", after_sequence=0)
    sequences = [chunk.sequence for chunk in snapshot.chunks]

    assert sequences == list(range(501, _MAX_CHUNKS + 501))
    assert snapshot.chunks[0].data == b"line-501\n"
    assert snapshot.chunks[-1].data == f"line-{_MAX_CHUNKS + 500}\n".encode()


@pytest.mark.asyncio
async def test_dropped_output_is_reported_rather_than_silently_missing(buffer) -> None:
    """The module docstring promises this and the code hard-coded it to 0."""
    await _emit(buffer, "p", _MAX_CHUNKS + 500)

    behind = await buffer.read("p", after_sequence=0)
    assert behind.truncated_before_sequence == 500

    caught_up = await buffer.read("p", after_sequence=behind.next_sequence)
    assert caught_up.chunks == ()
    assert caught_up.truncated_before_sequence == 0


@pytest.mark.asyncio
async def test_a_reader_within_the_window_is_told_nothing_was_dropped(buffer) -> None:
    await _emit(buffer, "p", 10)

    snapshot = await buffer.read("p", after_sequence=4)

    assert [chunk.sequence for chunk in snapshot.chunks] == list(range(5, 11))
    assert snapshot.truncated_before_sequence == 0
    assert snapshot.state is ProcessState.RUNNING


@pytest.mark.asyncio
async def test_recorded_exit_survives_a_later_read(buffer) -> None:
    await _emit(buffer, "p", 3)
    await buffer.record_exit("p", exit_code=0)

    snapshot = await buffer.read("p", after_sequence=0)

    assert snapshot.state is ProcessState.SUCCEEDED
    assert snapshot.exit_code == 0
    assert len(snapshot.chunks) == 3
