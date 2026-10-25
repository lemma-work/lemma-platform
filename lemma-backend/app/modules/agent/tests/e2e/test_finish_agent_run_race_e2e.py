"""Two parties ending the same run: exactly one of them does.

The worker finishing a run and the sweep that settles a run whose worker went
quiet can both reach `finish_agent_run` for one run. The read used to be
unlocked, so the sweep could read RUNNING, wait on the worker's row lock, and
then overwrite the COMPLETED it committed with FAILED -- reporting the run as
ended twice, which starts a second follow-up turn and wakes a waiting parent
again. Only real Postgres row locking can show this, so it runs against one.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import AgentRunStatus, ConversationStatus
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]


async def _running_run(db_session, scenario) -> UUID:
    await scenario.create_org_with_pod(name_prefix="Finish Race")
    conversation = ConversationModel(
        id=uuid4(),
        user_id=UUID(scenario.owner_user["id"]),
        pod_id=UUID(scenario.pod_id),
        organization_id=UUID(scenario.org_id),
        status=ConversationStatus.RUNNING.value,
    )
    db_session.add(conversation)
    run = AgentRunModel(
        id=uuid4(),
        conversation_id=conversation.id,
        status=AgentRunStatus.RUNNING.value,
        agent_runtime={"profile_id": "system:lemma"},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    return run.id


async def _waiting_on_a_lock(backend_pid: int) -> bool:
    async with async_session_maker() as session:
        waiting = await session.scalar(
            select(func.count())
            .select_from(text("pg_stat_activity"))
            .where(
                text("pid = :pid").bindparams(pid=backend_pid),
                text("wait_event_type = 'Lock'"),
            )
        )
    return bool(waiting)


async def test_a_sweep_racing_the_worker_does_not_end_the_run_a_second_time(
    db_session, scenario
) -> None:
    run_id = await _running_run(db_session, scenario)

    async with (
        create_uow_from_session_maker(async_session_maker) as worker,
        create_uow_from_session_maker(async_session_maker) as sweep,
    ):
        sweep_pid = int(await sweep.session.scalar(text("SELECT pg_backend_pid()")))
        finished = await Runs(worker).finish_agent_run(
            agent_run_id=run_id, status=AgentRunStatus.COMPLETED
        )
        # The worker holds the row, uncommitted; the sweep gets as far as it can.
        racing = asyncio.ensure_future(
            Runs(sweep).finish_agent_run(
                agent_run_id=run_id,
                status=AgentRunStatus.FAILED,
                error="Agent run was interrupted",
            )
        )
        await eventually(
            label="the sweep is blocked on the worker's row lock",
            probe=lambda: _waiting_on_a_lock(sweep_pid),
            done=bool,
            timeout_seconds=10,
        )
        await worker.commit()
        raced = await asyncio.wait_for(racing, timeout=10)
        await sweep.commit()

    assert finished is not None and finished.updated is True
    assert raced is not None
    assert raced.updated is False, "the run was reported as ended twice"
    assert raced.status is AgentRunStatus.COMPLETED

    async with async_session_maker() as session:
        stored = await session.get(AgentRunModel, run_id)
    assert stored is not None
    assert stored.status == AgentRunStatus.COMPLETED.value
    assert stored.error is None
