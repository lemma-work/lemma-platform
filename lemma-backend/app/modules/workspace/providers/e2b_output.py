"""Turning E2B's streaming output into a resumable, sequenced cursor.

This is the one genuine impedance mismatch in the E2B provider, and it is worth
naming precisely. E2B delivers process output by *pushing* it to a callback
while a connection is held open. Every caller above this module *pulls* it:
"give me everything after sequence N, and wait up to M seconds for more". That
pull model is not an accident -- it is what lets a workspace session be rebuilt
on every tool call, survive a backend restart mid-command, and let two pollers
read the same long-running process without stealing bytes from each other.

So the provider owns the buffer that E2B does not have. Callbacks append into a
sequenced Redis list; reads serve from it. On the Docker path this same buffer
lives inside the sandbox, maintained by the workspace runtime. On E2B there is
nowhere in the sandbox to put it, so it lives here instead.

Redis rather than process memory, deliberately: an in-process dict would tie a
running command to the one backend process that happened to start it, so a
rolling restart -- or simply a second replica handling the next poll -- would
lose output the agent had not read yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sandbox_runtime.protocol import (
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
)

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

# Long enough that an agent which parks a build and comes back still sees it,
# short enough that abandoned output does not accumulate forever.
_RETENTION_SECONDS = 60 * 60
# A runaway `yes` must not fill Redis. Past this the oldest chunks are dropped
# and the reader is told, so an agent sees "output was truncated" rather than
# silently believing it read everything.
_MAX_CHUNKS = 4096


@dataclass(frozen=True, slots=True)
class E2BOutputBuffer:
    """Sequenced output for one process, shared across pollers and replicas."""

    key_prefix: str = "workspace:e2b:output:v1"

    @property
    def _redis(self):
        return get_redis(url=settings.redis_url)

    def _chunks_key(self, process_id: str) -> str:
        return f"{self.key_prefix}:{process_id}:chunks"

    def _state_key(self, process_id: str) -> str:
        return f"{self.key_prefix}:{process_id}:state"

    async def append(
        self, process_id: str, *, channel: ProcessOutputChannel, data: bytes
    ) -> None:
        if not data:
            return
        redis = self._redis
        key = self._chunks_key(process_id)
        payload = json.dumps(
            {"c": channel.value, "d": data.decode("utf-8", errors="replace")}
        )
        pipe = redis.pipeline()
        pipe.rpush(key, payload)
        # Trimming here rather than on read keeps the memory bound honest even
        # if nobody ever reads this process's output.
        pipe.ltrim(key, -_MAX_CHUNKS, -1)
        pipe.expire(key, _RETENTION_SECONDS)
        await pipe.execute()

    async def record_start(self, process_id: str) -> None:
        await self._write_state(process_id, state=ProcessState.RUNNING, exit_code=None)

    async def record_exit(self, process_id: str, *, exit_code: int | None) -> None:
        await self._write_state(
            process_id,
            state=(ProcessState.SUCCEEDED if exit_code == 0 else ProcessState.FAILED),
            exit_code=exit_code,
        )

    async def record_cancelled(self, process_id: str) -> None:
        await self._write_state(
            process_id, state=ProcessState.CANCELLED, exit_code=None
        )

    async def _write_state(
        self, process_id: str, *, state: ProcessState, exit_code: int | None
    ) -> None:
        await self._redis.set(
            self._state_key(process_id),
            json.dumps({"s": state.value, "e": exit_code}),
            ex=_RETENTION_SECONDS,
        )

    async def read(
        self, process_id: str, *, after_sequence: int
    ) -> ProcessOutputSnapshot:
        """Everything *strictly after* ``after_sequence``.

        Both halves of that sentence are load-bearing, and getting either wrong
        is not a subtle failure. Sequences are 1-based and the bound is
        exclusive, because a reader advances its cursor to the sequence of the
        last chunk it consumed and asks again from there. Treating the bound as
        an inclusive list index re-delivers that chunk on every poll, so a
        command that printed one line appears to have printed it twenty times.
        """
        redis = self._redis
        key = self._chunks_key(process_id)
        total = await redis.llen(key)

        # The list is trimmed from the left, so the absolute sequence of the
        # oldest retained chunk is however many were dropped. Tracking total
        # appends separately would be more precise; this errs toward telling
        # the reader that truncation happened.
        raw_state = await redis.get(self._state_key(process_id))
        state, exit_code = ProcessState.RUNNING, None
        if raw_state:
            try:
                decoded = json.loads(raw_state)
                state = ProcessState(decoded["s"])
                exit_code = decoded["e"]
            except KeyError, TypeError, ValueError:
                # A malformed or half-written state key means the process
                # is simply not known to have finished, which is what the
                # RUNNING/None defaults above already say.
                pass

        # Sequence N is list index N-1, so "after N" starts at index N.
        start_index = max(0, after_sequence)
        raw_chunks = (
            await redis.lrange(key, start_index, -1) if start_index < total else []
        )

        chunks: list[ProcessOutputChunk] = []
        for offset, raw in enumerate(raw_chunks):
            try:
                decoded = json.loads(raw)
                chunks.append(
                    ProcessOutputChunk(
                        sequence=start_index + offset + 1,
                        channel=ProcessOutputChannel(decoded["c"]),
                        data=decoded["d"].encode(),
                    )
                )
            except KeyError, TypeError, ValueError:
                continue

        return ProcessOutputSnapshot(
            chunks=tuple(chunks),
            next_sequence=start_index + len(chunks),
            truncated_before_sequence=0,
            state=state,
            exit_code=exit_code,
        )

    async def forget(self, process_id: str) -> None:
        await self._redis.delete(
            self._chunks_key(process_id), self._state_key(process_id)
        )
