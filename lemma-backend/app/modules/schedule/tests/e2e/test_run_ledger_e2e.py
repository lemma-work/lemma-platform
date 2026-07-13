from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_agent,
    _create_pod,
    _create_schedule,
)

pytestmark = pytest.mark.e2e


async def test_run_ledger_concurrent_dedup(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async def claim_once():
        async with factory() as uow:
            return await ScheduleRunRepository(uow).claim(
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id="provider-event-42",
                target_kind="AGENT",
                payload={"ticket": 42},
                metadata={"provider": "test"},
                llm_output=None,
            )

    claims = await asyncio.gather(claim_once(), claim_once())
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    schedule_run = winners[0]

    async with factory() as uow:
        await ScheduleRunRepository(uow).mark_failed(
            schedule_run.id, RuntimeError("provider unavailable")
        )

    retried = await claim_once()
    assert retried is not None
    assert retried.id == schedule_run.id
    assert retried.target_run_id == schedule_run.target_run_id
    assert retried.attempts == 2
    async with factory() as uow:
        await ScheduleRunRepository(uow).mark_failed(
            retried.id, RuntimeError("provider still unavailable")
        )

    async with db_manager.session_factory() as session:
        persisted_schedule = await session.get(Schedule, schedule_id)
        assert persisted_schedule is not None
        assert persisted_schedule.consecutive_failures == 0

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs"
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["status"] == "FAILED"


async def test_concurrent_duplicate_target_outcomes_count_once(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async with factory() as uow:
        schedule_run = await ScheduleRunRepository(uow).claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id="concurrent-terminal-event",
            target_kind="AGENT",
            payload={},
            metadata=None,
            llm_output=None,
        )
        assert schedule_run is not None
        await ScheduleRunRepository(uow).mark_dispatched(schedule_run.id)

    async def fail_once() -> bool:
        async with factory() as uow:
            return await ScheduleRunOutcomeService(uow).record_target_outcome(
                target_kind="AGENT",
                target_run_id=schedule_run.target_run_id,
                status=ScheduleRunStatus.TARGET_FAILED,
                completed_at=None,
                error_type="AgentConversationFailed",
            )

    outcomes = await asyncio.gather(fail_once(), fail_once())
    assert sorted(outcomes) == [False, True]

    async with db_manager.session_factory() as session:
        persisted = await session.get(Schedule, schedule_id)
        assert persisted is not None
        assert persisted.consecutive_failures == 1


async def test_immediate_target_outcome_cannot_be_overwritten_by_dispatch(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async with factory() as uow:
        repository = ScheduleRunRepository(uow)
        schedule_run = await repository.claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id="immediate-terminal-target",
            target_kind="WORKFLOW",
            payload={},
            metadata=None,
            llm_output=None,
        )
        assert schedule_run is not None
        changed = await ScheduleRunOutcomeService(uow).record_target_outcome(
            target_kind="WORKFLOW",
            target_run_id=schedule_run.target_run_id,
            status=ScheduleRunStatus.COMPLETED,
            completed_at=None,
        )
        dispatched = await repository.mark_dispatched(schedule_run.id)

    assert changed is True
    assert dispatched is False

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs"
    )
    assert listed.status_code == 200, listed.text
    persisted_run = listed.json()["items"][0]
    assert persisted_run["status"] == ScheduleRunStatus.COMPLETED.value
    assert persisted_run["completed_at"] is not None
