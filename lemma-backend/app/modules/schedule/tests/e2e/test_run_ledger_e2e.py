from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_agent,
    _create_pod,
    _create_schedule,
)

pytestmark = pytest.mark.e2e


async def test_run_ledger_concurrent_dedup_and_retry_api(
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
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async def claim_once():
        async with factory() as uow:
            return await ScheduleRunRepository(uow).claim(
                schedule_id=schedule_id,
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

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs"
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["status"] == "FAILED"

    retried = await authenticated_client.post(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs/{schedule_run.id}/retry"
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == "RECEIVED"
