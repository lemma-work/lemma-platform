"""A schedule's filter end to end: a decision, asked once, and every outcome recorded.

No Typesafe key is configured in e2e and every model is the deterministic mock,
which answers a yes/no question with its smallest valid value, `false`. So here a
filtered schedule turns every event down, and the model rung is what says so --
which is exactly the path a deployment without System One takes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.handlers.schedule_consumer import handle_llm_filter_task
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.run_outcome_service import (
    FILTER_UNDECIDED,
    ScheduleRunOutcomeService,
)
from app.modules.schedule.services.run_recovery_service import (
    ScheduleRunRecoveryService,
)
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    SCHEDULE_E2E_TIMEOUT_SECONDS,
    _create_agent,
    _create_datastore_table,
    _create_pod,
    _create_schedule,
    _create_workflow,
    _seed_composio_webhook_trigger,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

_URGENT_ONLY = "Fire only for rows whose value is exactly 'urgent'."


async def _runs(client: AsyncClient, pod_id: str, schedule_id: str) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}/runs")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _schedule(client: AsyncClient, pod_id: str, schedule_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _decision(client: AsyncClient, pod_id: str, decision_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/decisions/{decision_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_datastore_filter_skip_is_a_filtered_run_carrying_its_decision(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
):
    """PS-SCHED-012 through the worker: skipped is recorded as skipped."""
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="filtered-datastore-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
        filter_instruction=_URGENT_ONLY,
    )

    record = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables/schedule_records/records",
        json={"data": {"source": "filtered-record", "value": "routine"}},
    )
    assert record.status_code == 201, record.text

    runs = await eventually(
        label="the filter's skip to reach the run ledger",
        probe=lambda: _runs(authenticated_client, pod_id, schedule["id"]),
        done=bool,
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
    )

    [run] = runs
    assert run["status"] == "FILTERED"
    assert run["llm_output"]["should_proceed"] is False
    assert run["payload"] == {}
    assert run["metadata"]["table_name"] == "schedule_records"
    assert run["target_run_id"] is None
    assert run["completed_at"] is not None

    detail = await _schedule(authenticated_client, pod_id, schedule["id"])
    assert detail["last_fire_status"] == "FILTERED"
    assert detail["consecutive_failures"] == 0

    decision = await _decision(
        authenticated_client, pod_id, run["llm_output"]["decision_id"]
    )
    assert decision["subject_key"] == (
        f"schedule:{schedule['id']}:{run['source_event_id']}"
    )
    assert decision["answers"]["proceed"]["value"] is False
    assert decision["answers"]["proceed"]["by"] == "model"
    # The row is on an RLS table, so it is its owner's alone -- and the
    # decision, which keeps it as evidence, is theirs alone too.
    assert decision["visibility"] == "PERSONAL"

    workflow_runs = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{workflow['name']}/runs"
    )
    assert workflow_runs.status_code == 200, workflow_runs.text
    assert workflow_runs.json()["items"] == []


async def test_a_webhook_filter_skip_is_recorded_once_however_often_it_arrives(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    """A redelivered event reads the recorded decision and keeps its one run."""
    await _seed_composio_webhook_trigger(db_session)
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "EVENT",
            "config": {
                "connector_id": "composio",
                "connector_trigger_id": "OUTLOOK_MESSAGE_TRIGGER",
                "trigger_config": {"source": "composio"},
            },
        },
        name_prefix="filtered-webhook-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config={"source": "composio", "provider_trigger_id": f"trg-{uuid4().hex[:8]}"},
        filter_instruction="Only mail from a customer describing a problem.",
    )
    source_event_id = f"composio:{uuid4().hex}"

    for _ in range(2):
        await handle_llm_filter_task(
            {"subject": "This week's newsletter"},
            {"source": "composio", "source_event_id": source_event_id},
            schedule_id=schedule["id"],
            source_event_id=source_event_id,
        )

    [run] = await _runs(authenticated_client, pod_id, schedule["id"])
    assert run["status"] == "FILTERED"
    assert run["source_event_id"] == source_event_id
    assert run["metadata"]["source"] == "composio"
    decisions = await authenticated_client.get(f"/pods/{pod_id}/decisions")
    assert decisions.status_code == 200, decisions.text
    [decision] = [
        item
        for item in decisions.json()["items"]
        if item["subject_key"] == f"schedule:{schedule['id']}:{source_event_id}"
    ]
    assert run["llm_output"]["decision_id"] == decision["id"]
    assert (await _schedule(authenticated_client, pod_id, schedule["id"]))[
        "last_fire_status"
    ] == "FILTERED"


async def test_undecided_filter_fires_trip_the_breaker_that_skips_never_touch(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_manager,
):
    """Five undecided fires pause the schedule, however many skips fall between.

    The breaker reads the newest completed runs through a bounded window. Skips
    complete too, so without being left out of that read, fifty skips after
    each failure would push the fifth failure out of the window and the
    breaker would never trip for a schedule that filters most of what it sees.
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    agent = await _create_agent(authenticated_client, pod_id)
    created = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        agent_name=agent["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
        filter_instruction=_URGENT_ONLY,
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        schedule = await ScheduleRepository(uow).get(UUID(created["id"]))
    assert isinstance(schedule, ScheduleEntity)

    for failure in range(5):
        async with factory() as uow:
            assert await ScheduleRunOutcomeService(uow).record_filter_undecided(
                schedule,
                source_event_id=f"undecided-{failure}",
                decision_id=uuid4(),
                metadata={"record_id": f"rec-{failure}"},
            )
        if failure == 4:
            break
        async with factory() as uow:
            outcomes = ScheduleRunOutcomeService(uow)
            for skip in range(50):
                assert await outcomes.record_filtered(
                    schedule,
                    source_event_id=f"skip-{failure}-{skip}",
                    user_id=schedule.user_id,
                    metadata=None,
                    llm_output={"should_proceed": False, "decision_id": str(uuid4())},
                )

    detail = await _schedule(authenticated_client, pod_id, created["id"])
    assert detail["is_active"] is False
    assert detail["consecutive_failures"] == 5
    assert detail["paused_by_failures"] is True
    assert detail["last_fire_status"] == "ERROR"
    assert "could not decide" in detail["last_error"]

    # The newest run is the fifth failure: listed as failed, not as skipped.
    latest = (await _runs(authenticated_client, pod_id, created["id"]))[0]
    assert latest["source_event_id"] == "undecided-4"
    assert latest["status"] == "DEAD_LETTERED"
    assert latest["error_type"] == FILTER_UNDECIDED
    assert latest["llm_output"]["decision_id"]
    assert latest["metadata"] == {"record_id": "rec-4"}


async def test_a_filtered_run_is_final_and_never_revisited(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_manager,
):
    """Written once, left alone by the recovery sweep, and not retryable."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    agent = await _create_agent(authenticated_client, pod_id)
    created = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        agent_name=agent["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
        filter_instruction=_URGENT_ONLY,
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        schedule = await ScheduleRepository(uow).get(UUID(created["id"]))
    assert isinstance(schedule, ScheduleEntity)
    verdict = {"should_proceed": False, "decision_id": str(uuid4())}

    async with factory() as uow:
        outcomes = ScheduleRunOutcomeService(uow)
        first = await outcomes.record_filtered(
            schedule,
            source_event_id="skip-once",
            user_id=schedule.user_id,
            metadata=None,
            llm_output=verdict,
        )
        again = await outcomes.record_filtered(
            schedule,
            source_event_id="skip-once",
            user_id=schedule.user_id,
            metadata=None,
            llm_output={"should_proceed": False, "decision_id": str(uuid4())},
        )
    assert (first, again) == (True, False)

    async with factory() as uow:
        swept = await ScheduleRunRecoveryService(uow).recover()
    assert (swept.redelivered, swept.reconciled, swept.dead_lettered) == (0, 0, 0)
    assert swept.still_running == 0
    # Outside the sweep's index, which is partial on `target_outcome IS NULL`:
    # a filter that skips most events must not put every skip in its path.
    async with factory() as uow:
        stored = await uow.session.scalar(
            select(ScheduleRun).where(
                ScheduleRun.schedule_id == schedule.id,
                ScheduleRun.source_event_id == "skip-once",
            )
        )
    assert stored is not None
    assert stored.target_outcome == "FILTERED"
    assert stored.last_inspected_at is None

    [run] = await _runs(authenticated_client, pod_id, created["id"])
    assert run["status"] == "FILTERED"
    assert run["llm_output"] == verdict
    retry = await authenticated_client.post(
        f"/pods/{pod_id}/schedules/{created['id']}/runs/{run['id']}/retry"
    )
    assert retry.status_code == 409, retry.text


async def test_a_time_schedule_refuses_a_filter_on_create_and_on_update(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)

    refused = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={"cron": "0 9 * * *"},
        filter_instruction="Only on weekdays.",
        expected_status=422,
    )
    assert "no event to judge" in str(refused)

    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={"cron": "0 9 * * *"},
    )
    update = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"filter_instruction": "Only on weekdays."},
    )
    assert update.status_code == 422, update.text
    assert update.json()["code"] == "SCHEDULE_VALIDATION_ERROR"
    assert (await _schedule(authenticated_client, pod_id, schedule["id"]))[
        "filter_instruction"
    ] is None
