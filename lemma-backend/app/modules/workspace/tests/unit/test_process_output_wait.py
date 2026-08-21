"""What a poll costs when the process has nothing more to say.

The agent's shell and Python tools were slow in a way that looked like the
sandbox being slow, and was not: a command producing no output paid a full
29-second poll window before returning. `mkdir`, `cd`, `touch` and most CLI
calls that print nothing all did this, so a handful of ordinary setup commands
turned into minutes of an agent apparently doing nothing.

The cause is a lost wakeup. `notify_all` only reaches waiters that are already
waiting, and a fast command exits *before* its first poll arrives — so the exit
notification went nowhere and the poll then waited out its whole window with
nothing left to wake it. Latching "finished" instead of signalling it is what
fixes that, and the timing assertions here are what stop it coming back.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from sandbox_runtime.protocol import ProcessOutputChannel
from sandbox_runtime.workspace.process_manager import OutputBuffer

pytestmark = pytest.mark.unit

# Generous next to the real 29s window, tight enough that a lost wakeup fails.
_IMMEDIATE_SECONDS = 0.5


def _buffer() -> OutputBuffer:
    return OutputBuffer(limit_bytes=1 << 20)


@pytest.mark.asyncio
async def test_a_silent_command_that_already_exited_returns_at_once() -> None:
    """The regression: exit happens before the first poll, which is the norm.

    A command finishes in milliseconds; the poll arrives after a network hop.
    """
    buffer = _buffer()
    await buffer.mark_finished()
    await asyncio.sleep(0.05)  # the poll arrives a moment later

    started = time.monotonic()
    snapshot = await buffer.snapshot(0, wait_seconds=29)
    elapsed = time.monotonic() - started

    assert elapsed < _IMMEDIATE_SECONDS, (
        f"polling a finished process blocked for {elapsed:.1f}s -- every silent "
        "command pays this"
    )
    assert snapshot.chunks == ()


@pytest.mark.asyncio
async def test_a_poll_still_waits_for_a_process_that_is_running() -> None:
    """The wait is the feature: one long poll instead of a tick loop."""
    buffer = _buffer()
    started = time.monotonic()
    snapshot = await buffer.snapshot(0, wait_seconds=0.4)
    elapsed = time.monotonic() - started

    assert 0.3 < elapsed, "a running, quiet process must not return instantly"
    assert snapshot.chunks == ()


@pytest.mark.asyncio
async def test_a_waiting_poll_wakes_the_moment_the_process_exits() -> None:
    buffer = _buffer()

    async def exit_soon() -> None:
        await asyncio.sleep(0.2)
        await buffer.mark_finished()

    task = asyncio.create_task(exit_soon())
    started = time.monotonic()
    await buffer.snapshot(0, wait_seconds=29)
    elapsed = time.monotonic() - started
    await asyncio.wait_for(task, timeout=5)

    assert elapsed < 1.0, f"woke {elapsed:.1f}s after exit, not promptly"


@pytest.mark.asyncio
async def test_a_waiting_poll_wakes_the_moment_output_appears() -> None:
    buffer = _buffer()

    async def speak() -> None:
        await asyncio.sleep(0.2)
        await buffer.append(ProcessOutputChannel.STDOUT, b"step 1\n")

    task = asyncio.create_task(speak())
    started = time.monotonic()
    snapshot = await buffer.snapshot(0, wait_seconds=29)
    elapsed = time.monotonic() - started
    await asyncio.wait_for(task, timeout=5)

    assert elapsed < 1.0, f"woke {elapsed:.1f}s after output, not promptly"
    assert len(snapshot.chunks) == 1


@pytest.mark.asyncio
async def test_output_already_buffered_is_returned_without_waiting() -> None:
    buffer = _buffer()
    await buffer.append(ProcessOutputChannel.STDOUT, b"done\n")

    started = time.monotonic()
    snapshot = await buffer.snapshot(0, wait_seconds=29)

    assert time.monotonic() - started < _IMMEDIATE_SECONDS
    assert len(snapshot.chunks) == 1


@pytest.mark.asyncio
async def test_a_finished_process_does_not_re_block_on_later_polls() -> None:
    """Draining output after exit takes several polls; none of them may wait."""
    buffer = _buffer()
    await buffer.append(ProcessOutputChannel.STDOUT, b"line\n")
    await buffer.mark_finished()

    started = time.monotonic()
    first = await buffer.snapshot(0, wait_seconds=29)
    # Second poll asks for everything after what it just read: nothing is left,
    # which is exactly the condition that used to block.
    second = await buffer.snapshot(first.next_sequence, wait_seconds=29)

    assert time.monotonic() - started < _IMMEDIATE_SECONDS
    assert second.chunks == ()


class TestWritingToATerminal:
    """Pasting more than the terminal buffer holds.

    A PTY master is non-blocking with a small kernel buffer (a few KB). Writing
    a file into `cat > file`, or a block of code into a REPL, is far more than
    that — the write has to be drained across several attempts rather than
    tried once. Measured against a real container before the fix: 11KB of a
    24KB paste arrived, and the tool reported failure that the caller was not
    checking.
    """

    @staticmethod
    async def _drain_writer(fd: int, data: bytes) -> None:
        """The production write loop, exercised directly on a real PTY."""
        from sandbox_runtime.workspace.process_manager import ManagedProcess

        managed = ManagedProcess.__new__(ManagedProcess)
        managed.master_fd = fd
        await ManagedProcess._write_to_pty(managed, data)

    @pytest.mark.asyncio
    async def test_a_paste_larger_than_the_buffer_is_written_in_full(self) -> None:
        import os
        import pty

        master, slave = pty.openpty()
        os.set_blocking(master, False)
        os.set_blocking(slave, False)
        # Newline-terminated: a terminal in canonical mode buffers until one
        # arrives, so an unbroken 24KB blob would stall on the line discipline
        # rather than on the buffer this test is about.
        payload = (b"x" * 199 + b"\n") * 120
        received = bytearray()

        async def slow_reader() -> None:
            """A child that consumes at its own pace, as a real one does."""
            while len(received) < len(payload):
                await asyncio.sleep(0.005)
                try:
                    received.extend(os.read(slave, 2048))
                except BlockingIOError, OSError:
                    continue

        reader = asyncio.create_task(slow_reader())
        try:
            await asyncio.wait_for(self._drain_writer(master, payload), timeout=15)
            await asyncio.wait_for(reader, timeout=15)
        finally:
            reader.cancel()
            os.close(master)
            os.close(slave)

        assert len(received) == len(payload), (
            f"only {len(received)} of {len(payload)} bytes reached the terminal"
        )

    @pytest.mark.asyncio
    async def test_a_small_write_needs_no_draining(self) -> None:
        import os
        import pty

        master, slave = pty.openpty()
        os.set_blocking(master, False)
        try:
            await asyncio.wait_for(self._drain_writer(master, b"ls -la\n"), timeout=5)
            assert os.read(slave, 4096) == b"ls -la\n"
        finally:
            os.close(master)
            os.close(slave)
