"""The per-sandbox record of processes this platform started on E2B.

E2B has no notion of "the processes Lemma started": its own listing reports
every pid inside the sandbox and cannot say which operation any of them belongs
to. So the provider keeps its own index in Redis, and this module owns what that
index stores per process.

Two fields carry weight beyond identifying the process.

The **deadline** is the only fact about a process that survives losing the watch
on it. `start_process` hands the same value to E2B as the process `timeout` and
E2B kills the process there, so past it the process is gone whatever our own
bookkeeping believes. That is what stops an unwatchable process pinning a paid
sandbox as busy forever.

The **working directory** is what tells one conversation's work from another's.
A sandbox belongs to a user, not a conversation, and several sessions may be
working in it at once -- each in its own directory. Without the cwd, the only
thing distinguishing them was a session binding held elsewhere, which is cleared
when a process completes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

PID_KEY_PREFIX = "workspace:e2b:pid:v1"
INDEX_KEY_PREFIX = "workspace:e2b:procs:v1"
# Long enough for an agent to park a build and come back to it.
ENTRY_TTL_SECONDS = 60 * 60
# The command is stored to make a listing readable, not to reproduce it. A
# heredoc or a long pipeline is identifiable well inside this.
_MAX_COMMAND_CHARS = 200


@dataclass(frozen=True, slots=True)
class ProcessEntry:
    pid: int
    tty: bool = False
    expires_at: float = 0.0
    cwd: str = ""
    command: str = ""
    started_at: float = 0.0


def pid_key(process_id: str) -> str:
    return f"{PID_KEY_PREFIX}:{process_id}"


def index_key(sandbox_id: str) -> str:
    return f"{INDEX_KEY_PREFIX}:{sandbox_id}"


def encode_entry(
    pid: int,
    *,
    tty: bool = False,
    expires_at: float = 0.0,
    cwd: str = "",
    command: str = "",
    started_at: float = 0.0,
) -> str:
    return json.dumps(
        {
            "pid": pid,
            "tty": int(tty),
            "exp": round(expires_at),
            "cwd": cwd,
            "cmd": command[:_MAX_COMMAND_CHARS],
            "at": round(started_at),
        }
    )


def decode_entry(raw) -> ProcessEntry:
    """Reads both shapes.

    Entries written before this was JSON are `pid:tty[:expires_at]`. They report
    no deadline rather than a wrong one -- reading a missing deadline as 0 would
    make every legacy process instantly expired, and expired means reclaimable.
    """
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    if text.startswith("{"):
        try:
            held = json.loads(text)
            return ProcessEntry(
                pid=int(held.get("pid", 0)),
                tty=bool(held.get("tty")),
                expires_at=float(held.get("exp", 0) or 0),
                cwd=str(held.get("cwd", "")),
                command=str(held.get("cmd", "")),
                started_at=float(held.get("at", 0) or 0),
            )
        except KeyError, TypeError, ValueError:
            return ProcessEntry(pid=0)
    parts = text.split(":")
    try:
        pid = int(parts[0])
    except ValueError:
        return ProcessEntry(pid=0)
    expires_at = 0.0
    if len(parts) > 2:
        try:
            expires_at = float(parts[2])
        except ValueError:
            expires_at = 0.0
    return ProcessEntry(
        pid=pid,
        tty=len(parts) > 1 and parts[1] == "1",
        expires_at=expires_at,
    )


def decode_pid(raw) -> tuple[int, bool]:
    entry = decode_entry(raw)
    return entry.pid, entry.tty


async def read_descriptors(redis, output, *, sandbox_id: str, now: float):
    """Every process this platform started in one sandbox, with a live state.

    Two corrections happen here, both driven by the recorded deadline.

    A process past its deadline is reported terminal even if our own record
    still says otherwise. Losing the watch on a process records UNKNOWN -- not
    terminal, deliberately, so a live command is never released out from under
    itself -- and without this bound that alone would pin a paid sandbox as busy
    for as long as the entry survived, doing no work the idle sweep could
    reclaim.

    And an entry both finished and past its deadline is dropped. Nothing ever
    removed these, while every new process refreshes the key's TTL, so the index
    grew for the whole life of the sandbox: the list an agent uses to recover a
    process id filled up with things that had ended hours earlier.
    """
    from datetime import datetime, timezone

    from sandbox_runtime.protocol import ProcessState

    from app.modules.workspace.process_output import TERMINAL_PROCESS_STATES
    from app.modules.workspace.providers.base import ProcessDescriptor

    key = index_key(sandbox_id)
    descriptors: list[ProcessDescriptor] = []
    stale: list[str] = []
    for raw_id, raw_entry in (await redis.hgetall(key)).items():
        process_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
        snapshot = await output.read(process_id, after_sequence=0)
        state = snapshot.state
        entry = decode_entry(raw_entry)
        past_deadline = bool(entry.expires_at) and now > entry.expires_at
        if past_deadline:
            if state in TERMINAL_PROCESS_STATES:
                stale.append(process_id)
            else:
                state = ProcessState.TIMED_OUT
        descriptors.append(
            ProcessDescriptor(
                process_id=process_id,
                state=state,
                exit_code=snapshot.exit_code,
                started_at=(
                    datetime.fromtimestamp(entry.started_at, timezone.utc)
                    if entry.started_at
                    else None
                ),
                cwd=entry.cwd,
                command=entry.command,
                tty=entry.tty,
            )
        )
    if stale:
        await redis.hdel(key, *stale)
    return tuple(descriptors)
