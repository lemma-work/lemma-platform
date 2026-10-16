"""Five hundred agent turns across fifty pods: start latency and fairness.

One pod is busy (59 turns), forty-nine are not (9 each). Every turn is started
through the API as production does and run by two in-process dispatchers (two
worker processes' worth of interactive slots) with the deterministic model held
two seconds per request. Measured per turn: how long its item waited between
becoming claimable (written, and the dispatchers running) and its handler
starting. What must hold: every turn completes
once, and a quiet pod is not starved by the busy one -- its p95 start latency
is no worse than the busy pod's.

Slow lane; numbers printed for the PR record.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.agent.events.turn_work import TurnFences, TurnRunner
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.harness_wiring import (
    build_harness_registry,
    worker_uow_factory,
)
from app.modules.agent.tests.e2e.turn_work_support import (
    rows,
    runtime_profile,
    runtime_sessions,
    scalar,
    turn_pod,
    turn_registry,
)
from app.modules.test_support.e2e.scripted_model import script_text

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(900)]

PODS = 50
BUSY_TURNS = 59
QUIET_TURNS = 9
SLOTS_PER_DISPATCHER = 16


def _p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


@pytest.mark.drives_work
async def test_five_hundred_turns_across_fifty_pods(
    authenticated_client, fixed_test_org, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "e2e_mock_llm_latency_ms", 2000)
    org = fixed_test_org["id"]
    profile = await runtime_profile(authenticated_client, org)
    pods = [
        await turn_pod(authenticated_client, org, profile_id=profile)
        for _ in range(PODS)
    ]
    busy = pods[0].pod_id

    started_runs: dict[str, str] = {}

    gate = asyncio.Semaphore(4)

    async def start(pod, count: int) -> None:
        async with gate:
            await _start(pod, count)

    async def _start(pod, count: int) -> None:
        for _ in range(count):
            conversation = await pod.conversation([script_text("done")])
            started = await pod.append(conversation)
            started_runs[started["agent_run_id"]] = pod.pod_id

    await asyncio.gather(
        *(start(pod, BUSY_TURNS if pod.pod_id == busy else QUIET_TURNS) for pod in pods)
    )
    assert len(started_runs) == BUSY_TURNS + (PODS - 1) * QUIET_TURNS == 500

    waited: dict[str, list[float]] = defaultdict(list)
    began_wall = datetime.now(UTC)

    def runner(fences: TurnFences) -> TurnRunner:
        real = AgentRunnerService(
            uow_factory=worker_uow_factory(),
            harness_registry=build_harness_registry(),
            terminal_units=fences.terminal,
            step_units=fences.steps,
        )
        execute = real.execute

        async def timed(**kwargs):
            pod_id = str(kwargs["pod_id"])
            created = await scalar(
                "SELECT created_at FROM agent_runs WHERE id = :r",
                r=kwargs["agent_run_id"],
            )
            # From when it could first have been claimed: its creation, or the
            # dispatchers starting, whichever was later.
            ready = max(created, began_wall)
            waited[pod_id].append((datetime.now(UTC) - ready).total_seconds())
            return await execute(**kwargs)

        real.execute = timed  # type: ignore[method-assign]
        return real

    registry = turn_registry(runner_factory=runner)
    dispatchers = [
        dispatcher_for(
            runtime_sessions(),
            WorkClass.INTERACTIVE,
            registry=registry,
            owner=f"worker-{n}",
            slots=SLOTS_PER_DISPATCHER,
        )
        for n in range(2)
    ]

    async def pump(dispatcher) -> None:
        running: set[asyncio.Task] = set()
        while True:
            claimed = await dispatcher.claim_once()
            for item in claimed:
                running.add(asyncio.create_task(dispatcher.execute(item)))
            if not claimed:
                if not running and not await scalar(
                    "SELECT count(*) FROM work_items WHERE kind = 'agent.turn'"
                ):
                    return
                if running:
                    _done, running = await asyncio.wait(
                        running, return_when=asyncio.FIRST_COMPLETED, timeout=0.2
                    )
                continue
            running = {task for task in running if not task.done()}

    began = time.monotonic()
    began_wall = datetime.now(UTC)
    await asyncio.wait_for(asyncio.gather(*(pump(d) for d in dispatchers)), 600)
    elapsed = time.monotonic() - began

    statuses = dict(
        await rows(
            "SELECT status, count(*) FROM agent_runs WHERE id = ANY(CAST(:ids AS uuid[])) "
            "GROUP BY status",
            ids=list(started_runs),
        )
    )
    assert statuses == {"COMPLETED": 500}, statuses
    quiet = [w for pod, values in waited.items() if pod != busy for w in values]
    loud = waited[busy]
    report = {
        "turns": 500,
        "elapsed_s": round(elapsed, 1),
        "throughput_per_s": round(500 / elapsed, 1),
        "start_latency_all_p50_s": round(_p(quiet + loud, 0.5), 2),
        "start_latency_all_p95_s": round(_p(quiet + loud, 0.95), 2),
        "quiet_pods_p95_s": round(_p(quiet, 0.95), 2),
        "busy_pod_p95_s": round(_p(loud, 0.95), 2),
        "quiet_pods_mean_s": round(statistics.mean(quiet), 2),
        "busy_pod_mean_s": round(statistics.mean(loud), 2),
    }
    print("\nLOAD", report)
    # Fair: the busy pod's backlog is not the quiet pods' wait.
    assert report["quiet_pods_p95_s"] <= report["busy_pod_p95_s"]
