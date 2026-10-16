"""A rolling restart of two real workers under in-flight agent turns.

Every turn completes, none answers twice, none repeats a tool step -- with the
workers' engines pooled, and unpooled (the e2e default), which is what pushes
the e2e Postgres to its connection limit while three workers overlap. Under
that pressure a heartbeat cannot always reach the database, leases lapse and
turns are taken over while their first worker still runs them: the claim's
epoch fences that worker's steps and end, and a refused connection is retried
as unavailability rather than failing the run.

``ROLLING_TURNS`` scales it (the verification ran 100); the default keeps the
slow lane short.
"""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import AsyncExitStack, asynccontextmanager
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

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.drives_work]

TURNS = int(os.environ.get("ROLLING_TURNS", "40"))
PODS = 3
LATENCY_MS = 2500


@asynccontextmanager
async def _worker(e2e_settings, name: str, *, pooled: bool):
    pool = {"DB_POOL_SIZE": "10", "DB_POOL_IN_TESTING": "true"} if pooled else {}
    async with production_worker_process(
        e2e_settings,
        log_prefix=f"rolling_{name}",
        worker_entrypoint="app.modules.agent.tests.e2e.turn_worker_entrypoint",
        extra_env={
            "E2E_TURN_LEASE_SECONDS": "10",
            "E2E_MOCK_LLM_LATENCY_MS": str(LATENCY_MS),
            "WORK_POLL_INTERVAL_SECONDS": "0.2",
            "WORK_SHUTDOWN_GRACE_JSON": '{"interactive": 30}',
            "WORK_CLASSES": "interactive,events",
            "WORK_DB_SLOTS_JSON": '{"interactive": 6, "events": 2}',
            **pool,
        },
    ) as worker:
        yield worker


async def _sample_counters(worst: dict[str, int], stop: asyncio.Event) -> None:
    while not stop.is_set():
        for attempts, losses in await rows(
            "SELECT attempts, lease_losses FROM work_items "
            "WHERE kind IN ('agent.turn', 'agent.turn.automation')"
        ):
            worst["attempts"] = max(worst["attempts"], attempts)
            worst["lease_losses"] = max(worst["lease_losses"], losses)
        try:
            await asyncio.wait_for(stop.wait(), 0.2)
        except TimeoutError:
            pass


@pytest.mark.parametrize("pooled", [True, False], ids=["pooled", "unpooled"])
async def test_a_rolling_restart_completes_every_turn_once(
    e2e_settings, authenticated_client, fixed_test_org, pooled: bool
) -> None:
    pods: list[TurnPod] = [
        await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])
        for _ in range(PODS)
    ]
    conversations = [
        (
            pods[i % PODS],
            await pods[i % PODS].conversation(
                [
                    script_tool_call("pod_tables", {}, tool_call_id=f"step-{i}"),
                    script_text(f"answer {i}"),
                ]
            ),
        )
        for i in range(TURNS)
    ]
    worst = {"attempts": 0, "lease_losses": 0}
    stop = asyncio.Event()
    sampler = asyncio.create_task(_sample_counters(worst, stop))
    run_ids: list[UUID] = []
    try:
        async with AsyncExitStack() as stack:
            first = await stack.enter_async_context(
                _worker(e2e_settings, "a1", pooled=pooled)
            )
            second = await stack.enter_async_context(
                _worker(e2e_settings, "b1", pooled=pooled)
            )
            for pod, conversation in conversations:
                run_ids.append(UUID((await pod.append(conversation))["agent_run_id"]))
            await eventually(
                label="turns running on both workers",
                probe=lambda: scalar(
                    "SELECT count(*) FROM work_items WHERE kind LIKE 'agent.turn%' "
                    "AND state = 'running'"
                ),
                done=lambda n: n >= min(10, TURNS // 2),
                timeout_seconds=60,
            )
            # Rolling: A down, A' up, then B down, B' up.
            for old, name in ((first, "a2"), (second, "b2")):
                old.process.send_signal(signal.SIGTERM)
                await asyncio.to_thread(old.process.wait, 90)
                await stack.enter_async_context(
                    _worker(e2e_settings, name, pooled=pooled)
                )
            await eventually(
                label="every run finished",
                probe=lambda: scalar(
                    "SELECT count(*) FROM agent_runs WHERE id = ANY(:ids) "
                    "AND status IN ('COMPLETED', 'FAILED', 'STOPPED')",
                    ids=run_ids,
                ),
                done=lambda n: n == TURNS,
                timeout_seconds=600,
                interval_seconds=1,
            )
    finally:
        stop.set()
        await sampler

    statuses = dict(
        await rows(
            "SELECT status, count(*) FROM agent_runs WHERE id = ANY(:ids) GROUP BY 1",
            ids=run_ids,
        )
    )
    repeated_steps = await scalar(
        "SELECT count(*) FROM (SELECT 1 FROM agent_messages "
        "WHERE agent_run_id = ANY(:ids) AND kind = 'TOOL_RETURN' "
        "GROUP BY agent_run_id, tool_call_id HAVING count(*) > 1) d",
        ids=run_ids,
    )
    answers = dict(
        await rows(
            "SELECT r, (SELECT count(*) FROM agent_messages m WHERE m.agent_run_id = r "
            "AND m.role = 'assistant' AND m.kind = 'TEXT') "
            "FROM unnest(CAST(:ids AS uuid[])) r",
            ids=[str(r) for r in run_ids],
        )
    )
    failures = await rows(
        "SELECT error, count(*) FROM agent_runs WHERE id = ANY(:ids) "
        "AND status <> 'COMPLETED' GROUP BY 1",
        ids=run_ids,
    )
    print(
        f"\nrolling restart ({'pooled' if pooled else 'unpooled'}): turns={TURNS} "
        f"statuses={statuses} repeated_steps={repeated_steps} "
        f"answers={sorted(set(answers.values()))} worst={worst}"
    )
    assert failures == [] and statuses == {"COMPLETED": TURNS}
    assert repeated_steps == 0
    assert set(answers.values()) == {1}, "every run answered exactly once"
