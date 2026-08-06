"""An in-memory stand-in for the Redis-backed E2B output buffer.

Only for tests. The real buffer is Redis-backed on purpose -- an in-process
dict would tie a running command to whichever backend replica started it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentbox.domain import (
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
)


@dataclass
class InMemoryOutputBuffer:
    chunks: dict[str, list[ProcessOutputChunk]] = field(default_factory=dict)
    states: dict[str, tuple[ProcessState, int | None]] = field(default_factory=dict)
    pids: dict[str, int] = field(default_factory=dict)

    async def append(
        self, process_id: str, *, channel: ProcessOutputChannel, data: bytes
    ) -> None:
        held = self.chunks.setdefault(process_id, [])
        held.append(
            ProcessOutputChunk(sequence=len(held), channel=channel, data=data)
        )

    async def record_start(self, process_id: str) -> None:
        self.states[process_id] = (ProcessState.RUNNING, None)

    async def record_exit(self, process_id: str, *, exit_code: int | None) -> None:
        self.states[process_id] = (
            ProcessState.SUCCEEDED if exit_code == 0 else ProcessState.FAILED,
            exit_code,
        )

    async def record_cancelled(self, process_id: str) -> None:
        self.states[process_id] = (ProcessState.CANCELLED, None)

    async def read(
        self, process_id: str, *, after_sequence: int
    ) -> ProcessOutputSnapshot:
        held = self.chunks.get(process_id, [])
        pending = held[max(0, after_sequence) :]
        state, exit_code = self.states.get(process_id, (ProcessState.RUNNING, None))
        return ProcessOutputSnapshot(
            chunks=tuple(pending),
            next_sequence=max(0, after_sequence) + len(pending),
            truncated_before_sequence=0,
            state=state,
            exit_code=exit_code,
        )

    async def forget(self, process_id: str) -> None:
        self.chunks.pop(process_id, None)
        self.states.pop(process_id, None)

    # Stand-ins for the provider's Redis-backed pid mapping.
    async def remember_pid(self, process_id: str, pid: int) -> None:
        self.pids[process_id] = pid

    async def recall_pid(self, process_id: str) -> int:
        return self.pids[process_id]

    def all_chunks(self) -> list[ProcessOutputChunk]:
        return [chunk for held in self.chunks.values() for chunk in held]
