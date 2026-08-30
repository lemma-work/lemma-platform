"""What `E2BOpsMixin.list_processes` reports, and for how long.

Nothing exercised this method before: the sweeper's own tests run against a
fake provider that *hands them* a correct process list, so the real
implementation -- which derives that list from a Redis mirror that can be wrong
in several ways -- was never under test. That is the shape where a fake
certifies the half that works.
"""

from __future__ import annotations

import time

import pytest
from fakeredis import aioredis as fake_aioredis

from sandbox_runtime.protocol import ProcessOutputChannel, ProcessState

from app.modules.workspace.providers import e2b_ops, e2b_output, e2b_process_index
from app.modules.workspace.providers.base import ProviderInstance
from app.modules.workspace.providers.e2b_ops import E2BOpsMixin
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer

_SANDBOX = "i8fdef5eyd8zxnysl6bor"
_KEY = f"workspace:e2b:procs:v1:{_SANDBOX}"


class _Ops(E2BOpsMixin):
    def __init__(self) -> None:
        self._output = E2BOutputBuffer()


@pytest.fixture
def ops(monkeypatch) -> _Ops:
    fake = fake_aioredis.FakeRedis()
    monkeypatch.setattr(e2b_output, "get_redis", lambda **_kwargs: fake)
    monkeypatch.setattr(e2b_ops.E2BOpsMixin, "_redis", staticmethod(lambda: fake))
    return _Ops()


def _instance() -> ProviderInstance:
    return ProviderInstance(provider_id=_SANDBOX, name="lemma-ws-abc-1", running=True)


@pytest.mark.asyncio
async def test_a_process_whose_watch_was_lost_stops_pinning_past_its_deadline(
    ops,
) -> None:
    """The cost half of recording a lost watch as UNKNOWN.

    UNKNOWN is deliberately not terminal, so a live command is never released
    out from under itself. On its own that would pin a paid sandbox as busy for
    as long as the index entry survived, doing no work the idle sweep could
    reclaim. The deadline bounds it: `start_process` hands that same value to
    E2B as the process timeout, so past it the process is gone.
    """
    await ops._remember_pid(
        "proc-lost",
        4242,
        tty=False,
        sandbox_id=_SANDBOX,
        expires_at=time.time() - 5,
    )
    await ops._output.record_unknown("proc-lost")

    processes = await ops.list_processes(_instance(), deadline_at=None)

    assert [p.state for p in processes] == [ProcessState.TIMED_OUT]


@pytest.mark.asyncio
async def test_a_lost_watch_before_the_deadline_still_counts_as_running(ops) -> None:
    """The correctness half: inside its deadline the command may well be alive,
    and releasing the sandbox would destroy it."""
    await ops._remember_pid(
        "proc-live",
        4242,
        tty=False,
        sandbox_id=_SANDBOX,
        expires_at=time.time() + 600,
    )
    await ops._output.record_unknown("proc-live")

    processes = await ops.list_processes(_instance(), deadline_at=None)

    assert [p.state for p in processes] == [ProcessState.UNKNOWN]


@pytest.mark.asyncio
async def test_processes_over_and_past_their_deadline_leave_the_index(ops) -> None:
    """Nothing pruned this, and every new process refreshes the key's TTL, so
    the index grew for the whole life of the sandbox."""
    fake = ops._redis()
    await ops._remember_pid(
        "proc-old", 1, tty=False, sandbox_id=_SANDBOX, expires_at=time.time() - 60
    )
    await ops._output.record_exit("proc-old", exit_code=0)
    await ops._remember_pid(
        "proc-recent", 2, tty=False, sandbox_id=_SANDBOX, expires_at=time.time() + 600
    )
    await ops._output.record_exit("proc-recent", exit_code=0)

    await ops.list_processes(_instance(), deadline_at=None)

    remaining = {
        key.decode() if isinstance(key, bytes) else key
        for key in await fake.hgetall(_KEY)
    }
    # The recent one stays: an agent must still be able to read the outcome of
    # a command it has only just finished.
    assert remaining == {"proc-recent"}


@pytest.mark.asyncio
async def test_a_running_process_is_reported_running(ops) -> None:
    await ops._remember_pid(
        "proc-run", 7, tty=True, sandbox_id=_SANDBOX, expires_at=time.time() + 600
    )
    await ops._output.record_start("proc-run")
    await ops._output.append(
        "proc-run", channel=ProcessOutputChannel.STDOUT, data=b"working\n"
    )

    processes = await ops.list_processes(_instance(), deadline_at=None)

    assert [(p.process_id, p.state) for p in processes] == [
        ("proc-run", ProcessState.RUNNING)
    ]


@pytest.mark.asyncio
async def test_an_entry_written_before_deadlines_were_recorded_still_decodes(
    ops,
) -> None:
    """Old two-field entries carry no deadline; they must not read as expired."""
    fake = ops._redis()
    await fake.hset(_KEY, "proc-legacy", "99:1")
    await ops._output.record_start("proc-legacy")

    processes = await ops.list_processes(_instance(), deadline_at=None)

    assert [p.state for p in processes] == [ProcessState.RUNNING]
    assert e2b_process_index.decode_pid(b"99:1") == (99, True)
