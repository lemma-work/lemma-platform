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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from sandbox_runtime.protocol import ProcessState

from app.modules.workspace.process_output import TERMINAL_PROCESS_STATES
from app.modules.workspace.providers.base import ProcessDescriptor

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


#: Asks the sandbox itself which pids are alive, or None when it cannot say.
#: Called only when an entry's watch was lost, because it is a round trip to
#: E2B rather than to Redis.
LivePids = Callable[[], Awaitable[frozenset[int] | None]]


async def read_descriptors(
    redis,
    output,
    *,
    sandbox_id: str,
    now: float,
    live_pids: LivePids | None = None,
) -> tuple[ProcessDescriptor, ...]:
    """Every process this platform started in one sandbox, with a live state.

    Three corrections happen here, all driven by what outlives our own record.

    A process past its deadline is reported terminal even if our own record
    still says otherwise. Losing the watch on a process records UNKNOWN -- not
    terminal, deliberately, so a live command is never released out from under
    itself -- and without this bound that alone would pin a paid sandbox as busy
    for as long as the entry survived, doing no work the idle sweep could
    reclaim.

    Inside its deadline, a lost watch is settled by asking E2B whether the pid
    is still alive (``live_pids``). The exit is recorded only by a task in the
    worker that started the command, so a restart of that worker otherwise left
    a finished command reading as running for its whole lifetime.

    And an entry is dropped once it is over: finished and past its deadline, or
    with nothing recorded about it any more. Its state ages out of Redis on its
    own clock, and one entry whose state had gone used to make this whole
    listing raise -- every wait on every process in the sandbox read as
    unreadable, and the idle sweep could never release it.

    State is read on its own (`read_state`), never with the output: a listing
    has no use for the output, and reading it renewed every abandoned buffer.
    """
    key = index_key(sandbox_id)
    descriptors: list[ProcessDescriptor] = []
    stale: list[str] = []
    unwatched: dict[str, int] = {}
    for raw_id, raw_entry in (await redis.hgetall(key)).items():
        process_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
        entry = decode_entry(raw_entry)
        descriptor, over = _describe(
            process_id, entry, await output.read_state(process_id), now=now
        )
        if over:
            stale.append(process_id)
        if descriptor is None:
            continue
        if descriptor.state is ProcessState.UNKNOWN and entry.pid:
            unwatched[process_id] = entry.pid
        descriptors.append(descriptor)
    if stale:
        await redis.hdel(key, *stale)
    if unwatched and live_pids is not None:
        alive = await live_pids()
        if alive is not None:
            descriptors = [
                _settled(descriptor, unwatched, alive) for descriptor in descriptors
            ]
    return tuple(descriptors)


def _describe(
    process_id: str,
    entry: ProcessEntry,
    recorded: tuple[ProcessState, int | None] | None,
    *,
    now: float,
) -> tuple[ProcessDescriptor | None, bool]:
    """One entry as a descriptor, and whether it is over and can leave the index.

    None when nothing about it is left to report.
    """
    past_deadline = bool(entry.expires_at) and now > entry.expires_at
    if recorded is None:
        if past_deadline or not entry.expires_at:
            return None, True
        # Inside its deadline with nothing recorded: E2B may still be running
        # it, so the caller asks the sandbox.
        recorded = (ProcessState.UNKNOWN, None)
    state, exit_code = recorded
    over = past_deadline and state in TERMINAL_PROCESS_STATES
    if past_deadline and not over:
        state = ProcessState.TIMED_OUT
    descriptor = ProcessDescriptor(
        process_id=process_id,
        state=state,
        exit_code=exit_code,
        started_at=(
            datetime.fromtimestamp(entry.started_at, timezone.utc)
            if entry.started_at
            else None
        ),
        cwd=entry.cwd,
        command=entry.command,
        tty=entry.tty,
    )
    return descriptor, over


def _settled(
    descriptor: ProcessDescriptor, unwatched: dict[str, int], alive: frozenset[int]
) -> ProcessDescriptor:
    """A lost watch, answered by the sandbox's own process table.

    Alive reads as RUNNING, which it is. Absent reads as ended with no exit
    code, which is all anyone can know. When the sandbox could not be asked the
    caller leaves the entry UNKNOWN, so a reader that cannot tell says so rather
    than guessing either way.
    """
    pid = unwatched.get(descriptor.process_id)
    if pid is None:
        return descriptor
    if pid in alive:
        return replace(descriptor, state=ProcessState.RUNNING)
    return replace(descriptor, exited_unobserved=True)
