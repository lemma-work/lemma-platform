"""A finished command must not be billed the whole yield window.

The reader woke on new output and on nothing else. Exit is recorded by a
separate watcher task, so it lands *after* the last chunk: the collector's
first poll took the output while the state was still RUNNING, came back, saw a
non-terminal state and polled again -- and this loop then had no new bytes to
wait for and no reason to stop, so it slept out the remaining window. Every
command that printed something and then exited paid that in full.

Measured on a real workspace: `lemma tables list` ran in 771 ms while the tool
call around it took 23-39 s. Agents read that as a hang -- they poll, give up,
kill the process and start over -- which is what it looked like from the UI.
"""

from __future__ import annotations

import asyncio

import pytest

from sandbox_runtime.protocol import ProcessOutputSnapshot, ProcessState
from app.modules.workspace.providers.e2b_ops import E2BOpsMixin


class _Buffer:
    """Reports a process that has already exited, with its output drained."""

    def __init__(self, state: ProcessState) -> None:
        self.state = state
        self.reads = 0

    async def read(self, process_id: str, *, after_sequence: int):
        del process_id, after_sequence
        self.reads += 1
        return ProcessOutputSnapshot(
            chunks=(),
            next_sequence=1,
            truncated_before_sequence=None,
            state=self.state,
            exit_code=0 if self.state is ProcessState.SUCCEEDED else 1,
        )


class _Ops(E2BOpsMixin):
    def __init__(self, buffer: _Buffer) -> None:
        self._output = buffer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        ProcessState.SUCCEEDED,
        ProcessState.FAILED,
        ProcessState.CANCELLED,
        ProcessState.TIMED_OUT,
    ],
)
async def test_a_finished_process_returns_at_once(state: ProcessState) -> None:
    buffer = _Buffer(state)
    ops = _Ops(buffer)

    started = asyncio.get_running_loop().time()
    snapshot = await ops.read_process_output(
        object(),  # type: ignore[arg-type]
        process_id="op-1",
        after_sequence=5,
        wait_seconds=30.0,
        deadline_at=None,  # type: ignore[arg-type]
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert snapshot.state is state
    assert elapsed < 1.0, f"waited {elapsed:.1f}s for a process that had exited"


@pytest.mark.asyncio
async def test_a_running_process_with_no_output_still_waits() -> None:
    """The wait is what makes a quiet long-running command cost one request."""
    buffer = _Buffer(ProcessState.RUNNING)
    ops = _Ops(buffer)

    started = asyncio.get_running_loop().time()
    await ops.read_process_output(
        object(),  # type: ignore[arg-type]
        process_id="op-1",
        after_sequence=5,
        wait_seconds=0.4,
        deadline_at=None,  # type: ignore[arg-type]
    )

    assert asyncio.get_running_loop().time() - started >= 0.3
    assert buffer.reads > 1, "it must keep looking while the process is alive"
