"""Output a process dropped before it was read is said to be missing.

A process's output is kept in a bounded buffer that drops its oldest chunks to
stay under its limit and says where what is left begins
(``truncated_before_sequence``). A burst past the limit between two reads --
a Desktop host started with the smaller limit an old host is given, a sandbox's
own ring -- used to come back as if it were all of it: the cursor moved past
the gap and nothing said anything was missing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from sandbox_runtime.protocol import (
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
)
from app.modules.workspace.process_output import (
    DROPPED_OUTPUT_MARKER,
    collect_process_output,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Finished:
    """A process that has exited, answering with what its buffer kept."""

    def __init__(self, chunks: tuple[ProcessOutputChunk, ...], truncated: int | None):
        self._snapshot = ProcessOutputSnapshot(
            chunks=chunks,
            next_sequence=(chunks[-1].sequence + 1) if chunks else 1,
            truncated_before_sequence=truncated,
            state=ProcessState.SUCCEEDED,
            exit_code=0,
        )

    async def read_process_output(self, *_args, **_kwargs) -> ProcessOutputSnapshot:
        return self._snapshot


class _Cursor:
    def __init__(self, at: int = 0) -> None:
        self.at = at

    async def load(self, _operation_id) -> int:
        return self.at

    def remember_locally(self, _operation_id, sequence) -> None:
        self.at = sequence

    async def save(self, _operation_id, sequence) -> None:
        self.at = sequence


def _out(sequence: int, text: str) -> ProcessOutputChunk:
    return ProcessOutputChunk(
        sequence=sequence, channel=ProcessOutputChannel.STDOUT, data=text.encode()
    )


async def _collect(client, cursor: _Cursor) -> dict:
    return await collect_process_output(
        client,
        "workspace",
        cursor,
        uuid4(),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        yield_time_ms=None,
    )


async def test_output_dropped_before_it_was_read_is_marked_where_it_was():
    # The reader had read up to 1; chunks 2-9 were dropped, 10 and 11 kept.
    client = _Finished((_out(10, "tail one\n"), _out(11, "tail two\n")), truncated=10)

    result = await _collect(client, _Cursor(at=1))

    assert result["output_truncated"] is True
    assert result["stdout"] == DROPPED_OUTPUT_MARKER.decode() + "tail one\ntail two\n"
    assert result["completed"] is True


async def test_a_drop_the_reader_had_already_read_past_is_not_a_gap():
    # Dropped up to 3, but this reader had read through 4 already.
    client = _Finished((_out(5, "next\n"),), truncated=3)

    result = await _collect(client, _Cursor(at=4))

    assert result["output_truncated"] is False
    assert result["stdout"] == "next\n"


async def test_nothing_dropped_reads_as_it_was_printed():
    client = _Finished((_out(1, "all of it\n"),), truncated=None)

    result = await _collect(client, _Cursor())

    assert result["output_truncated"] is False
    assert result["stdout"] == "all of it\n"
