"""Collecting a process's output within one call's wait window.

Split out of `SandboxWorkspaceSession` because it is the one piece of that class
with a non-obvious contract: it decides how long to wait, and how long to ask
the *server* to wait, from two different clocks. Getting that wrong is not a
slow poll — it is a transport error reported to the agent as a failed command.
"""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any
from uuid import UUID

from sandbox_runtime.protocol import ProcessState
from sandbox_runtime.protocol import WorkloadKind

# The in-sandbox runtime caps a single output wait at 30s, and the runtime HTTP
# client caps a request at 35s. Waiting the full 30 keeps the number of requests
# for a quiet process to a minimum.
_MAX_OUTPUT_WAIT_SECONDS = 30.0

# Headroom between how long the server is asked to hold a response and how long
# the client will wait for it, so a wait never expires in transit.
_POLL_SAFETY_MARGIN_SECONDS = 1.0

TERMINAL_PROCESS_STATES = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}


async def collect_process_output(
    client,
    logical_id,
    output_cursor,
    operation_id: UUID,
    *,
    deadline_at: datetime,
    yield_time_ms: int | None,
) -> dict[str, Any]:
    started = time.monotonic()
    yield_seconds = None if yield_time_ms is None else yield_time_ms / 1000
    after_sequence = await output_cursor.load(operation_id)
    initial_sequence = after_sequence
    stdout = bytearray()
    stderr = bytearray()
    state = ProcessState.RUNNING
    exit_code: int | None = None
    polled = False
    while datetime.now(timezone.utc) < deadline_at:
        elapsed = time.monotonic() - started
        if yield_seconds is not None and elapsed >= yield_seconds:
            break

        # Wait out as much of the remaining window as the transport allows
        # rather than ticking every second: the server returns as soon as
        # output appears, so a quiet 30s wait costs one request, not thirty.
        # The safety margin is load-bearing — the HTTP timeout comes off the
        # same deadline, and asking the server to hold longer than the
        # client waits turns "still running" into a transport error. It is
        # charged against the deadline alone. Taking it out of the caller's
        # yield window as well put a cliff at the margin: any yield at or
        # under a second became a single non-waiting poll, so a command that
        # finished in milliseconds still came back "still running" and cost
        # the caller another round trip to find out otherwise.
        window = (
            deadline_at - datetime.now(timezone.utc)
        ).total_seconds() - _POLL_SAFETY_MARGIN_SECONDS
        if yield_seconds is not None:
            window = min(window, yield_seconds - elapsed)
        wait_seconds = min(window, _MAX_OUTPUT_WAIT_SECONDS)
        if wait_seconds <= 0:
            # A very short yield still wants whatever is already buffered, so
            # read once — but never twice, or this becomes the busy loop it
            # replaced.
            if polled:
                break
            wait_seconds = 0.0

        polled = True
        snapshot = await client.read_process_output(
            WorkloadKind.WORKSPACE,
            logical_id,
            operation_id,
            deadline_at=deadline_at,
            after_sequence=after_sequence,
            wait_seconds=wait_seconds,
        )
        state = snapshot.state
        exit_code = snapshot.exit_code
        for chunk in snapshot.chunks:
            after_sequence = max(after_sequence, chunk.sequence)
            if chunk.channel.value == "stderr":
                stderr.extend(chunk.data)
            else:
                stdout.extend(chunk.data)
        output_cursor.remember_locally(operation_id, after_sequence)
        if state in TERMINAL_PROCESS_STATES:
            break
    completed = state in TERMINAL_PROCESS_STATES
    if after_sequence != initial_sequence:
        await output_cursor.save(operation_id, after_sequence)
    # Each poll's bytes are decoded as a unit, so a chunk boundary inside
    # one poll is handled correctly. A multi-byte character split across
    # two polls still yields one replacement character; holding the partial
    # sequence back is not worth it, because the output cursor is a single
    # sequence over interleaved stdout/stderr chunks and rewinding it could
    # duplicate or drop real output.
    return {
        "success": state in {ProcessState.RUNNING, ProcessState.SUCCEEDED},
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "exit_code": exit_code,
        "completed": completed,
        "process_id": None if completed else str(operation_id),
        "error": None
        if state in {ProcessState.RUNNING, ProcessState.SUCCEEDED}
        else state.value,
    }
