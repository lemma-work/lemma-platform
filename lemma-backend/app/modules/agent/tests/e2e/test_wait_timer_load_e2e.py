"""Load: a thousand ``wait_for`` timers across many pods coming due in one minute.

Each conversation is paused on a TIME wait whose ``agent.wait.fire`` item is
due somewhere in the same 60-second window. One ``WorkRuntime`` -- the worker's
own dispatch loops and sized pool, events class only -- fires them: wait
COMPLETED, tool return, resume run, item deleted, one transaction each.

Measured, and printed as one JSON line (``WAIT_TIMER_LOAD_RESULT``): lateness
(``completed_at - scheduled_at``) percentiles, and the runtime's pool size. Sized by ``WAIT_LOAD_WAITS``, ``WAIT_LOAD_PODS`` and
``WAIT_LOAD_WINDOW_SECONDS``; the default is small enough for the protected
lane, and ``WAIT_LOAD_WAITS=1000 WAIT_LOAD_PODS=100`` is the full measurement.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkClass, WorkWriter
from app.core.infrastructure.work.db import DbBudget
from app.core.infrastructure.work.runtime import WorkRuntime
from app.modules.agent.domain.pausing_tools import WAIT_TOOL_NAME
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    MessageDraft,
)
from app.modules.agent.domain.wait import AgentConversationWaitEntity, AgentWaitType
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.wait_timers import arm_wait
from app.modules.test_support.e2e.agent_work import agent_wait_and_approval_registry
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

WAITS = int(os.environ.get("WAIT_LOAD_WAITS", "200"))
PODS = int(os.environ.get("WAIT_LOAD_PODS", "10"))
WINDOW = timedelta(seconds=int(os.environ.get("WAIT_LOAD_WINDOW_SECONDS", "60")))
_RUNTIME = AgentRuntimeConfig(profile_id="system:lemma")


async def _conversation(scenario: E2EScenario, pod_id: str, agent: str) -> UUID:
    created = await scenario.owner_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent, "title": "Load", "type": "CHAT"},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return UUID(created.json()["id"])


async def _pod_with_agent(scenario: E2EScenario) -> tuple[str, str]:
    pod = await scenario.create_pod(name_prefix="Wait load")
    agent = f"waiter-{uuid4().hex[:8]}"
    created = await scenario.owner_client.post(
        f"/pods/{pod['id']}/agents",
        json={
            "name": agent,
            "instruction": "Answer in plain text.",
            "agent_runtime": _RUNTIME.model_dump(mode="json"),
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return str(pod["id"]), agent


async def _pause(conversation_id: UUID, pod_id: UUID, user_id: UUID) -> None:
    """A run paused on ``wait_for``, its wait and its item, not yet due."""
    far = datetime.now(timezone.utc) + timedelta(days=1)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages = Messages(uow)
        runs = Runs(uow)
        run = await runs.create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=_RUNTIME,
            metadata={"source": "e2e_wait_load"},
        )
        call = f"call-{uuid4().hex[:12]}"
        await messages.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_tool_call(
                tool_name=WAIT_TOOL_NAME,
                tool_call_id=call,
                tool_args={"reason": "load", "seconds": 60},
            ),
        )
        await runs.finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.WAITING,
        )
        wait = await AgentConversationWaitRepository(uow).create(
            AgentConversationWaitEntity(
                conversation_id=conversation_id,
                agent_run_id=run.id,
                pod_id=pod_id,
                tool_call_id=call,
                wait_type=AgentWaitType.TIME,
                external_ref=str(uuid4()),
                scheduled_at=far,
                spec={"reason": "load", "user_id": str(user_id)},
            )
        )
        await arm_wait(WorkWriter.of(uow), wait, far)
        await uow.commit()


async def _scalar(sql: str, **params: object) -> object:
    async with async_session_maker() as session:
        return (await session.execute(text(sql), params)).scalar_one()


async def test_a_thousand_timers_due_in_one_minute_fire_on_time(
    scenario, e2e_settings
) -> None:
    await scenario.create_org(name_prefix="Wait load")
    user_id = UUID(scenario.owner_user["id"])
    per_pod = WAITS // PODS
    for _ in range(PODS):
        pod_id, agent = await _pod_with_agent(scenario)
        conversations = await asyncio.gather(
            *(_conversation(scenario, pod_id, agent) for _ in range(per_pod))
        )
        for start in range(0, len(conversations), 10):
            await asyncio.gather(
                *(
                    _pause(c, UUID(pod_id), user_id)
                    for c in conversations[start : start + 10]
                )
            )
    total = per_pod * PODS

    # Spread the due times evenly over one window starting a few seconds out,
    # in the order the waits were written (so the pods interleave).
    window_start = datetime.now(timezone.utc) + timedelta(seconds=5)
    async with async_session_maker() as session:
        await session.execute(
            text(
                "WITH ranked AS (SELECT id, row_number() OVER (ORDER BY random()) - 1 "
                "AS i FROM agent_conversation_waits WHERE status = 'ACTIVE') "
                "UPDATE agent_conversation_waits w SET scheduled_at = "
                "CAST(:start AS timestamptz) + r.i * make_interval(secs => :step) "
                "FROM ranked r WHERE w.id = r.id"
            ),
            {
                "start": window_start,
                "step": WINDOW.total_seconds() / total,
            },
        )
        await session.execute(
            text(
                "UPDATE work_items i SET run_at = w.scheduled_at "
                "FROM agent_conversation_waits w "
                "WHERE i.dedup_key = 'wait:' || w.id::text"
            )
        )
        await session.commit()

    budget = DbBudget.from_settings([WorkClass.EVENTS])
    engine = create_async_engine(
        e2e_settings.database_url, pool_size=budget.total, max_overflow=0
    )
    runtime = WorkRuntime(
        async_sessionmaker(engine, expire_on_commit=False),
        classes=[WorkClass.EVENTS],
        registry=agent_wait_and_approval_registry(),
        budget=budget,
    )
    began = time.monotonic()
    await runtime.start()
    try:
        await eventually(
            label="every wait fired",
            probe=lambda: _scalar(
                "SELECT count(*) FROM agent_conversation_waits "
                "WHERE status = 'COMPLETED'"
            ),
            done=lambda n: int(n) >= total,  # type: ignore[call-overload]
            timeout_seconds=WINDOW.total_seconds() + 120,
            interval_seconds=1.0,
            fail_fast=lambda _: None,
        )
        elapsed = time.monotonic() - began
    finally:
        await runtime.stop()
        await engine.dispose()

    lateness = await _scalar(
        "SELECT json_build_object("
        "'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY s), "
        "'p95', percentile_cont(0.95) WITHIN GROUP (ORDER BY s), "
        "'p99', percentile_cont(0.99) WITHIN GROUP (ORDER BY s), "
        "'max', max(s), 'min', min(s), 'n', count(*)) FROM ("
        "SELECT extract(epoch FROM completed_at - scheduled_at) AS s "
        "FROM agent_conversation_waits WHERE status = 'COMPLETED') t"
    )
    assert isinstance(lateness, dict)
    resumed = await _scalar(
        "SELECT count(*) FROM agent_runs WHERE run_metadata->>'source' = 'wait_resume'"
    )
    dead = await _scalar("SELECT count(*) FROM work_items WHERE state = 'dead'")
    result = {
        "waits": total,
        "pods": PODS,
        "window_seconds": WINDOW.total_seconds(),
        "seconds": round(elapsed, 1),
        "lateness_seconds": {k: round(float(v or 0), 3) for k, v in lateness.items()},
        "resume_runs": resumed,
        "dead_items": dead,
        "runtime_pool": budget.total,
    }
    print("WAIT_TIMER_LOAD_RESULT " + json.dumps(result))
    assert resumed == total, "a wait woke without exactly one resume run"
    assert dead == 0
    assert float(lateness["min"] or 0) >= 0, "a timer fired before it was due"
