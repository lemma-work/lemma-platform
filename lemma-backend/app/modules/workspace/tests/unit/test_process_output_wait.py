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
fixes that, and these tests are what stop it coming back.

"At once" here is counted in event-loop turns, not seconds. A poll that already
has its answer never waits, so it is done once the loop has run what was ready;
a lost wakeup leaves it waiting on its window however many turns go by. A busy
runner can take as long as it likes over each turn without changing which.
"""

from __future__ import annotations

import asyncio

import pytest

from sandbox_runtime.protocol import ProcessOutputChannel
from sandbox_runtime.workspace.process_manager import OutputBuffer, OutputSnapshot

pytestmark = pytest.mark.unit


def _buffer() -> OutputBuffer:
    return OutputBuffer(limit_bytes=1 << 20)


def _poll(
    buffer: OutputBuffer, after_sequence: int = 0
) -> asyncio.Task[OutputSnapshot]:
    """A poll with the real 29-second window, which nothing here waits out."""
    return asyncio.create_task(buffer.snapshot(after_sequence, wait_seconds=29))


async def _settle() -> None:
    """Give everything that is ready its turn. A wakeup takes one or two."""
    for _ in range(10):
        await asyncio.sleep(0)


async def _abandon(poll: asyncio.Task[OutputSnapshot]) -> None:
    poll.cancel()
    # The CancelledError is the expected outcome, so it is collected.
    await asyncio.gather(poll, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_silent_command_that_already_exited_returns_at_once() -> None:
    """The regression: exit happens before the first poll, which is the norm.

    A command finishes in milliseconds; the poll arrives after a network hop.
    """
    buffer = _buffer()
    await buffer.mark_finished()

    poll = _poll(buffer)
    await _settle()

    assert poll.done(), (
        "polling a finished process waits out its window -- every silent "
        "command pays this"
    )
    assert poll.result().chunks == ()


@pytest.mark.asyncio
async def test_a_poll_still_waits_for_a_process_that_is_running() -> None:
    """The wait is the feature: one long poll instead of a tick loop."""
    buffer = _buffer()

    poll = _poll(buffer)
    await _settle()

    assert not poll.done(), "a running, quiet process must not return instantly"
    await _abandon(poll)
    # And a window that runs out ends the wait with nothing to show.
    assert (await buffer.snapshot(0, wait_seconds=0.01)).chunks == ()


@pytest.mark.asyncio
async def test_a_waiting_poll_wakes_the_moment_the_process_exits() -> None:
    buffer = _buffer()
    poll = _poll(buffer)
    await _settle()
    assert not poll.done()

    await buffer.mark_finished()
    await _settle()

    assert poll.done(), "the exit did not wake the poll waiting on it"


@pytest.mark.asyncio
async def test_a_waiting_poll_wakes_the_moment_output_appears() -> None:
    buffer = _buffer()
    poll = _poll(buffer)
    await _settle()
    assert not poll.done()

    await buffer.append(ProcessOutputChannel.STDOUT, b"step 1\n")
    await _settle()

    assert poll.done(), "the output did not wake the poll waiting on it"
    assert len(poll.result().chunks) == 1


@pytest.mark.asyncio
async def test_output_already_buffered_is_returned_without_waiting() -> None:
    buffer = _buffer()
    await buffer.append(ProcessOutputChannel.STDOUT, b"done\n")

    poll = _poll(buffer)
    await _settle()

    assert poll.done()
    assert len(poll.result().chunks) == 1


@pytest.mark.asyncio
async def test_a_finished_process_does_not_re_block_on_later_polls() -> None:
    """Draining output after exit takes several polls; none of them may wait."""
    buffer = _buffer()
    await buffer.append(ProcessOutputChannel.STDOUT, b"line\n")
    await buffer.mark_finished()

    first = _poll(buffer)
    await _settle()
    assert first.done()
    # Second poll asks for everything after what it just read: nothing is left,
    # which is exactly the condition that used to block.
    second = _poll(buffer, first.result().next_sequence)
    await _settle()

    assert second.done()
    assert second.result().chunks == ()


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
