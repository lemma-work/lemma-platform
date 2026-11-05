"""Agent turns on a real worker process: killed, shut down, stopped.

The API (this process) starts the turn; ``python -m app.worker`` runs it from
the item the start wrote. Nothing is pumped by the test. Three properties the
streaq path could not hold:

* **SIGKILL**: the turn's lease runs out and another worker resumes the run
  from its last committed step -- the step that finished is not repeated, the
  run finishes once, and it happens within a lease, not a ten-minute sweep;
* **SIGTERM**: the worker stops the turn at its next step boundary and hands it
  back uncharged (no attempt, no lost lease); the next worker resumes it at
  once;
* **Stop** reaches a turn that is busy inside a model request.

A shorter lease (``turn_worker_entrypoint``) keeps the crash test in seconds:
the heartbeat and the reclaim scale with it, nothing else changes.
"""

from __future__ import annotations

import os
import signal
import time
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    rows,
    scalar,
    turn_pod,
)
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.worker_process import production_worker_process

# Slow and in the protected lane: each test owns its worker processes, which
# must not share an e2e process with the session-scoped base worker.
pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.drives_work]

LEASE_SECONDS = 3.0


@asynccontextmanager
async def _worker(e2e_settings, name: str, *, latency_ms: int):
    async with production_worker_process(
        e2e_settings,
        log_prefix=f"turn_worker_{name}",
        worker_entrypoint="app.modules.agent.tests.e2e.turn_worker_entrypoint",
        extra_env={
            "E2E_TURN_LEASE_SECONDS": str(LEASE_SECONDS),
            "E2E_MOCK_LLM_LATENCY_MS": str(latency_ms),
            "WORK_POLL_INTERVAL_SECONDS": "0.2",
            # Room for a model request to reach its step boundary.
            "WORK_SHUTDOWN_GRACE_JSON": '{"interactive": 30}',
        },
    ) as worker:
        yield worker


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


async def _turn(run_id: str) -> tuple | None:
    found = await rows(
        "SELECT state, attempts, lease_losses FROM work_items WHERE dedup_key = :k",
        k=f"agent-run:{run_id}",
    )
    return tuple(found[0]) if found else None


async def _returns(run_id: str, call_id: str) -> int:
    return await scalar(
        "SELECT count(*) FROM agent_messages WHERE agent_run_id = :r "
        "AND kind = 'TOOL_RETURN' AND tool_call_id = :c",
        r=UUID(run_id),
        c=call_id,
    )


async def _unpaired(run_id: str) -> int:
    return await scalar(
        "SELECT count(*) FROM agent_messages c WHERE c.agent_run_id = :r "
        "AND c.kind = 'TOOL_CALL' AND NOT EXISTS (SELECT 1 FROM agent_messages t "
        "WHERE t.agent_run_id = :r AND t.kind = 'TOOL_RETURN' "
        "AND t.tool_call_id = c.tool_call_id)",
        r=UUID(run_id),
    )


async def _answers(run_id: str) -> int:
    return await scalar(
        "SELECT count(*) FROM agent_messages WHERE agent_run_id = :r "
        "AND role = 'assistant' AND kind = 'TEXT'",
        r=UUID(run_id),
    )


async def test_a_killed_worker_turn_resumes_from_its_last_step_within_a_lease(
    e2e_settings, pod: TurnPod
) -> None:
    conversation = await pod.conversation(
        [
            script_tool_call("pod_tables", {}, tool_call_id="before-crash"),
            script_text("Finished after the crash."),
        ]
    )
    async with _worker(e2e_settings, "killed", latency_ms=4000) as first:
        run_id = (await pod.append(conversation))["agent_run_id"]
        # Killed inside the second model request: the first step (the call and
        # its return) is committed, the answer is not.
        await eventually(
            label="the first step committed",
            probe=lambda: _returns(run_id, "before-crash"),
            done=lambda n: n == 1,
            timeout_seconds=30,
        )
        os.kill(first.process.pid, signal.SIGKILL)
        first.process.wait(timeout=10)
        killed_at = time.monotonic()

    assert await pod.run_status(run_id) == "RUNNING"
    assert (await _turn(run_id))[0] == "running", "held by a dead worker"

    async with _worker(e2e_settings, "resumer", latency_ms=200) as second:
        ready_at = time.monotonic()
        status = await eventually(
            label="the run finished on the second worker",
            probe=lambda: pod.run_status(run_id),
            done=lambda s: s in {"COMPLETED", "FAILED", "STOPPED"},
            timeout_seconds=60,
            fail_fast=lambda _s: (
                second.read_log_tail() if second.process.poll() is not None else None
            ),
        )
        finished_at = time.monotonic()
        log = second.log_path.read_text()

    assert status == "COMPLETED"
    assert '"event": "agent.turn.resuming"' in log
    assert '"lease_losses": 1' in log
    # The step that finished before the crash is not repeated.
    assert await _returns(run_id, "before-crash") == 1
    assert await _answers(run_id) == 1
    # The lease ran out while the next worker was starting: it reclaims the
    # turn on its first reap and finishes it.
    assert finished_at - ready_at < LEASE_SECONDS + 12
    print(
        f"\nSIGKILL -> finished {finished_at - killed_at:.1f}s after the kill, "
        f"{finished_at - ready_at:.1f}s after the next worker was ready"
    )


async def test_a_shut_down_worker_hands_its_turn_back_uncharged(
    e2e_settings, pod: TurnPod
) -> None:
    """SIGTERM while a model request is in flight: the turn finishes that step,
    hands itself back at the boundary, and the next worker resumes it.

    The signal is sent once the first step's return is committed, so the turn
    is inside its harness. Sent as soon as the item was merely claimed, it could
    land before the turn had begun: handed back just as uncharged, but with no
    step to stop after -- which is what made this test flaky.
    """
    conversation = await pod.conversation(
        [
            script_tool_call("pod_tables", {}, tool_call_id="before-yield"),
            script_tool_call("pod_tables", {}, tool_call_id="in-flight"),
            script_text("Finished on the next worker."),
        ]
    )
    async with _worker(e2e_settings, "draining", latency_ms=3000) as first:
        run_id = (await pod.append(conversation))["agent_run_id"]
        await eventually(
            label="the first step committed",
            probe=lambda: _returns(run_id, "before-yield"),
            done=lambda n: n == 1,
            timeout_seconds=30,
            interval_seconds=0.05,
        )
        first.process.send_signal(signal.SIGTERM)
        signalled_at = time.monotonic()
        released = await eventually(
            label="the turn was handed back",
            probe=lambda: _turn(run_id),
            done=lambda t: t is not None and t[0] == "queued",
            timeout_seconds=30,
            interval_seconds=0.05,
        )
        released_after = time.monotonic() - signalled_at
        first.process.wait(timeout=60)
        log = first.log_path.read_text()

    # Uncharged: a deploy is not the turn's failure.
    assert released == ("queued", 0, 0)
    assert '"event": "agent.turn.yielded"' in log
    assert await pod.run_status(run_id) == "RUNNING"
    # It stopped between steps: every call it made has its return.
    assert await _unpaired(run_id) == 0
    assert await _returns(run_id, "before-yield") == 1

    async with _worker(e2e_settings, "next", latency_ms=200):
        restarted_at = time.monotonic()
        status = await eventually(
            label="the run finished on the next worker",
            probe=lambda: pod.run_status(run_id),
            done=lambda s: s in {"COMPLETED", "FAILED", "STOPPED"},
            timeout_seconds=60,
        )
        finished_after = time.monotonic() - restarted_at

    assert status == "COMPLETED"
    assert await _returns(run_id, "before-yield") == 1
    assert await _returns(run_id, "in-flight") == 1
    assert await _answers(run_id) == 1
    print(
        f"\nSIGTERM -> handed back in {released_after:.2f}s; "
        f"finished {finished_after:.2f}s after the next worker started"
    )


async def test_a_stop_reaches_a_turn_busy_inside_the_model(
    e2e_settings, pod: TurnPod
) -> None:
    """Every stop check sits between streamed chunks; a model request has none.

    The mock model is held inside one request for a minute. The stop has to
    land in a small fraction of that.
    """
    conversation = await pod.conversation([script_text("never finished")])
    async with _worker(e2e_settings, "stopped", latency_ms=60_000):
        run_id = (await pod.append(conversation))["agent_run_id"]
        await eventually(
            label="the turn is running",
            probe=lambda: _turn(run_id),
            done=lambda t: t is not None and t[0] == "running",
            timeout_seconds=30,
        )
        asked_at = time.monotonic()
        stopped = await pod.stop(conversation)
        assert stopped["status"] == "STOP_REQUESTED"
        await eventually(
            label="the busy turn stopped",
            probe=lambda: pod.run_status(run_id),
            done=lambda s: s == "STOPPED",
            timeout_seconds=20,
        )
        took = time.monotonic() - asked_at

    assert took < 15
    assert await _turn(run_id) is None, "the item completed with the stop"
