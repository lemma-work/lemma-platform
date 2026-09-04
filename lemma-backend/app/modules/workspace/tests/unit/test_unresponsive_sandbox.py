"""A sandbox that has stopped answering must say so, not read as "still running".

The failure this covers reached users. A workspace sandbox had been alive for
days, accumulated a leaked browser, and ran out of memory; commands still
*started* -- E2B returned a pid in 260ms -- but nothing they printed ever came
back and they never exited. Every layer below behaved correctly, and the tool
call returned `success: true, completed: false, stdout: ""`.

That result is indistinguishable from a quiet build, so the agent did the only
thing it could: poll, give up, kill the process, run it again, and collect
another silent window. Three tool calls, ninety seconds, no information. The
sandbox was only recovered when a person noticed and destroyed it by hand.

The distinguishing question is cheap -- can this sandbox still run `echo`? --
and these tests are what make sure it is asked, and asked only when the answer
is worth a round trip.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from sandbox_runtime.protocol import ProcessOutputSnapshot, ProcessState
from app.modules.workspace import process_output
from app.modules.workspace.process_output import collect_process_output

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# The production threshold is five seconds, which would make every test here
# cost that much wall clock to say nothing. The boundary itself is what these
# exercise, so it is moved rather than waited out.
_SUSPICIOUS_AFTER = 0.3


@pytest.fixture(autouse=True)
def _quick_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process_output, "_SILENCE_IS_SUSPICIOUS_AFTER_SECONDS", _SUSPICIOUS_AFTER
    )


class _SilentClient:
    """A process that never prints and never exits, answering instantly."""

    def __init__(self, state: ProcessState = ProcessState.RUNNING) -> None:
        self._state = state
        self.polls = 0

    async def read_process_output(self, *_args, **_kwargs) -> ProcessOutputSnapshot:
        self.polls += 1
        return ProcessOutputSnapshot(
            chunks=(),
            next_sequence=0,
            truncated_before_sequence=None,
            state=self._state,
            exit_code=None,
        )


class _Cursor:
    def __init__(self) -> None:
        self.saved: list[int] = []

    async def load(self, _operation_id) -> int:
        return 0

    def remember_locally(self, _operation_id, _sequence) -> None:
        return None

    async def save(self, _operation_id, sequence) -> None:
        self.saved.append(sequence)


def _deadline(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def _collect(client, *, yield_ms: int, probe):
    return await collect_process_output(
        client,
        uuid4(),
        _Cursor(),
        uuid4(),
        deadline_at=_deadline(30),
        yield_time_ms=yield_ms,
        probe_liveness=probe,
    )


async def test_a_silent_window_on_a_dead_sandbox_is_reported_as_failure() -> None:
    probed = []

    async def probe() -> bool:
        probed.append(True)
        return False

    result = await _collect(_SilentClient(), yield_ms=500, probe=probe)

    assert probed, "a fully silent window must ask whether the sandbox is alive"
    assert result["success"] is False
    assert result["completed"] is False
    assert "stopped responding" in result["error"]
    assert "Restart the workspace sandbox" in result["error"], (
        "the caller has to be told the recovery, or it polls the corpse again"
    )


async def test_a_silent_window_on_a_live_sandbox_is_still_just_running() -> None:
    """A quiet build is the common case and must not be turned into an error."""

    async def probe() -> bool:
        return True

    result = await _collect(_SilentClient(), yield_ms=500, probe=probe)

    assert result["success"] is True
    assert result["completed"] is False
    assert result["error"] is None


async def test_a_short_window_does_not_pay_for_a_probe() -> None:
    """Half a second of quiet says nothing, so it must not cost a round trip."""
    probed = []

    async def probe() -> bool:
        probed.append(True)
        return False

    result = await _collect(_SilentClient(), yield_ms=100, probe=probe)

    assert probed == []
    assert result["success"] is True


async def test_a_window_that_produced_output_is_never_probed() -> None:
    """Bytes arriving are proof of life; asking again would be pure cost."""

    class _Talking(_SilentClient):
        """Prints once early, then goes quiet -- a build that logs a header."""

        async def read_process_output(self, *_args, **_kwargs):
            from sandbox_runtime.protocol import (
                ProcessOutputChannel,
                ProcessOutputChunk,
            )

            self.polls += 1
            chunks = ()
            if self.polls == 1:
                chunks = (
                    ProcessOutputChunk(
                        sequence=1,
                        channel=ProcessOutputChannel.STDOUT,
                        data=b"working\n",
                    ),
                )
            return ProcessOutputSnapshot(
                chunks=chunks,
                next_sequence=2,
                truncated_before_sequence=None,
                state=ProcessState.RUNNING,
                exit_code=None,
            )

    probed = []

    async def probe() -> bool:
        probed.append(True)
        return False

    result = await _collect(_Talking(), yield_ms=500, probe=probe)

    assert probed == []
    assert result["stdout"] == "working\n"
