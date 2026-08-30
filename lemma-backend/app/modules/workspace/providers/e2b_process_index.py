"""The per-sandbox record of processes this platform started on E2B.

E2B has no notion of "the processes Lemma started": its own listing reports
every pid inside the sandbox and cannot say which operation any of them belongs
to. So the provider keeps its own index in Redis, and this module owns the one
thing that index stores per process.

The deadline is the load-bearing part. `start_process` hands the same value to
E2B as the process `timeout`, and E2B kills the process there. That makes it the
only fact about a process that survives losing the watch on it: past the
deadline the process is gone, whatever our own bookkeeping believes, which is
what stops an unwatchable process pinning a paid sandbox as busy forever.
"""

from __future__ import annotations

PID_KEY_PREFIX = "workspace:e2b:pid:v1"
INDEX_KEY_PREFIX = "workspace:e2b:procs:v1"
# Long enough for an agent to park a build and come back to it.
ENTRY_TTL_SECONDS = 60 * 60


def pid_key(process_id: str) -> str:
    return f"{PID_KEY_PREFIX}:{process_id}"


def index_key(sandbox_id: str) -> str:
    return f"{INDEX_KEY_PREFIX}:{sandbox_id}"


def encode_entry(pid: int, *, tty: bool, expires_at: float = 0.0) -> str:
    return f"{pid}:{int(tty)}:{expires_at:.0f}"


def decode_entry(raw) -> tuple[int, bool, float]:
    """`pid:tty[:expires_at]`.

    Entries written before the deadline was recorded carry no third field, and
    report no deadline rather than a wrong one -- reading a missing deadline as
    0 would make every such process instantly expired.
    """
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    parts = text.split(":")
    pid = int(parts[0])
    tty = len(parts) > 1 and parts[1] == "1"
    expires_at = 0.0
    if len(parts) > 2:
        try:
            expires_at = float(parts[2])
        except ValueError:
            expires_at = 0.0
    return pid, tty, expires_at


def decode_pid(raw) -> tuple[int, bool]:
    pid, tty, _expires_at = decode_entry(raw)
    return pid, tty


async def read_descriptors(redis, output, *, sandbox_id: str, now: float):
    """Every process this platform started in one sandbox, with a live state.

    Two corrections happen here, both driven by the recorded deadline.

    A process past its deadline is reported terminal even if our own record
    still says otherwise. Losing the watch on a process records UNKNOWN -- not
    terminal, deliberately, so a live command is never released out from under
    itself -- and without this bound that alone would pin a paid sandbox as
    busy for as long as the entry survived, doing no work the idle sweep could
    reclaim.

    And an entry both finished and past its deadline is dropped. Nothing ever
    removed these, while every new process refreshes the key's TTL, so the
    index grew for the whole life of the sandbox: the list an agent uses to
    recover a process id filled up with things that had ended hours earlier.
    """
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
        _pid, _tty, expires_at = decode_entry(raw_entry)
        past_deadline = bool(expires_at) and now > expires_at
        if past_deadline:
            if state in TERMINAL_PROCESS_STATES:
                stale.append(process_id)
            else:
                state = ProcessState.TIMED_OUT
        descriptors.append(
            ProcessDescriptor(
                process_id=process_id, state=state, exit_code=snapshot.exit_code
            )
        )
    if stale:
        await redis.hdel(key, *stale)
    return tuple(descriptors)
