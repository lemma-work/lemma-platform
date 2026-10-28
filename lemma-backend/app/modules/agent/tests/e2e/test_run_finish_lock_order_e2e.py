"""Ending a run and starting the next turn take their locks in one order.

Every transaction that starts, resumes, retries or stops a turn locks the
conversation and then its active run. Ending a run updated the run first and
the conversation after, so an approval answered while the run that asked for
it was still being written as finished -- the resume holding the conversation
and waiting for the run, the finish holding the run and waiting for the
conversation -- was a cycle, and Postgres aborted one side.

These tests make the interleaving certain rather than likely: the resume's
lock is held, the other side is started and seen waiting, and only then does
the resume reach for the run. With the old order the finish already held the
run by then, and one of the two died on a deadlock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]


async def _running_run(db_session, scenario: E2EScenario) -> tuple[UUID, UUID]:
    await scenario.create_org_with_pod(name_prefix="Lock order")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "lock order"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    run = AgentRunModel(
        conversation_id=conversation_id,
        status=AgentRunStatus.RUNNING.value,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    return conversation_id, run.id


async def _blocked_on_conversations() -> int:
    """Backends waiting on a row lock while running a statement on conversations."""
    async with async_session_maker() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%conversations%' "
                    "AND query NOT ILIKE '%pg_stat_activity%'"
                )
            )
        ).scalar_one()


async def _in_own_transaction(
    work: Callable[[Runs], Awaitable[object]],
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await work(Runs(uow))
        await uow.commit()


async def _finish(repository: Runs, run_id: UUID) -> None:
    """What the runner's finalizer does when a run pauses on an approval."""
    await repository.finish_agent_run(
        agent_run_id=run_id, status=AgentRunStatus.COMPLETED
    )


async def _stop(repository: Runs, conversation_id: UUID) -> None:
    """What Stop does: take the active run, then ask it to stop."""
    active = await repository.get_active_agent_run_for_update(conversation_id)
    assert active is not None
    await repository.finish_agent_run(
        agent_run_id=active.id, status=AgentRunStatus.STOP_REQUESTED
    )


@pytest.mark.parametrize("other_side", ["finish", "stop"])
async def test_a_turn_holding_the_conversation_and_a_run_ending_do_not_deadlock(
    db_session, scenario, other_side: str
) -> None:
    conversation_id, run_id = await _running_run(db_session, scenario)

    async with create_uow_from_session_maker(async_session_maker) as turn:
        conversations = Conversations(turn)
        runs = Runs(turn)
        # What `start_resume_run_if_ready` does first.
        await conversations.lock_conversation(conversation_id)

        ending = asyncio.create_task(
            _in_own_transaction(
                (lambda repo: _finish(repo, run_id))
                if other_side == "finish"
                else (lambda repo: _stop(repo, conversation_id))
            )
        )
        await eventually(
            label=f"the {other_side} waiting on the conversation the turn holds",
            probe=_blocked_on_conversations,
            done=lambda waiting: waiting >= 1,
            timeout_seconds=15,
        )
        assert not ending.done()
        # ...and then what it does next. Under the old order the other side
        # already held this row, and Postgres aborted one of the two here.
        active = await asyncio.wait_for(
            runs.get_active_agent_run_for_update(conversation_id), timeout=30
        )
        assert active is not None and active.id == run_id
        await turn.commit()

    await asyncio.wait_for(ending, timeout=30)

    async with async_session_maker() as session:
        status = await session.scalar(
            select(AgentRunModel.status).where(AgentRunModel.id == run_id)
        )
        conversation_status = await session.scalar(
            select(ConversationModel.status).where(
                ConversationModel.id == conversation_id
            )
        )
    expected = (
        AgentRunStatus.COMPLETED
        if other_side == "finish"
        else AgentRunStatus.STOP_REQUESTED
    )
    assert status == expected.value
    assert conversation_status is not None
