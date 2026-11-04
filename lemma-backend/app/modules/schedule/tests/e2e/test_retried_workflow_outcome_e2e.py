"""A scheduled workflow run that failed, was retried from its step, and finished.

The retry keeps the run's id, so the schedule's ledger hears a second terminal
outcome for a run it already recorded as failed. These pin what that second
outcome may change: the run's reported status and the failure streak -- and
what it may not: a schedule the breaker already turned off stays off.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_pod,
    _create_schedule,
    _create_workflow,
)

pytestmark = pytest.mark.e2e

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _workflow_schedule(client, org_id: str) -> tuple[str, UUID, UUID]:
    pod_id = await _create_pod(client, org_id)
    workflow = await _create_workflow(
        client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "CRON"}},
        name_prefix="retried-outcome",
    )
    schedule = await _create_schedule(
        client,
        pod_id,
        schedule_type="TIME",
        workflow_name=workflow["name"],
        config={"cron": "0 0 * * *"},
    )
    return pod_id, UUID(schedule["id"]), UUID(schedule["user_id"])


async def _dispatched(
    factory, *, schedule_id: UUID, user_id: UUID, key: str, kind: str
):
    async with factory() as uow:
        repository = ScheduleRunRepository(uow)
        run = await repository.claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id=key,
            target_kind=kind,
            payload={},
            metadata=None,
            llm_output=None,
        )
        assert run is not None
        await repository.mark_dispatched(run.id)
    return run


async def _outcome(
    factory,
    run,
    status: ScheduleRunStatus,
    completed_at: datetime | None,
    *,
    kind: str = "WORKFLOW",
) -> bool:
    async with factory() as uow:
        return await ScheduleRunOutcomeService(uow).record_target_outcome(
            target_kind=kind,
            target_run_id=run.target_run_id,
            status=status,
            completed_at=completed_at,
            error_type=(
                "WorkflowRunFailed"
                if status is ScheduleRunStatus.TARGET_FAILED
                else None
            ),
        )


async def _schedule_row(db_manager, schedule_id: UUID) -> Schedule:
    async with db_manager.session_factory() as session:
        row = await session.get(Schedule, schedule_id)
    assert row is not None
    return row


async def test_a_retried_workflow_that_succeeds_replaces_its_failure(
    authenticated_client, fixed_test_org, db_manager
) -> None:
    pod_id, schedule_id, user_id = await _workflow_schedule(
        authenticated_client, fixed_test_org["id"]
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    run = await _dispatched(
        factory, schedule_id=schedule_id, user_id=user_id, key="fire", kind="WORKFLOW"
    )
    failed_at = _BASE + timedelta(minutes=1)
    assert await _outcome(factory, run, ScheduleRunStatus.TARGET_FAILED, failed_at)
    assert (await _schedule_row(db_manager, schedule_id)).consecutive_failures == 1

    retried_at = failed_at + timedelta(minutes=5)
    assert await _outcome(factory, run, ScheduleRunStatus.COMPLETED, retried_at)

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs"
    )
    assert listed.status_code == 200, listed.text
    (reported,) = listed.json()["items"]
    assert reported["status"] == ScheduleRunStatus.COMPLETED.value
    assert reported["error_type"] is None
    row = await _schedule_row(db_manager, schedule_id)
    assert row.consecutive_failures == 0
    assert row.is_active is True


async def test_a_workflow_reporting_naive_times_is_read_as_utc(
    authenticated_client, fixed_test_org, db_manager
) -> None:
    """A workflow run's times have no zone, and its terminal event says so.

    Comparing one with the recorded aware time raised, so the retried success
    was never recorded; storing one let the database read it as local time.
    """
    _, schedule_id, user_id = await _workflow_schedule(
        authenticated_client, fixed_test_org["id"]
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    run = await _dispatched(
        factory, schedule_id=schedule_id, user_id=user_id, key="fire", kind="WORKFLOW"
    )
    failed_at = datetime(2026, 1, 1, 12, 0)
    assert await _outcome(factory, run, ScheduleRunStatus.TARGET_FAILED, failed_at)

    retried_at = failed_at + timedelta(minutes=5)
    assert await _outcome(factory, run, ScheduleRunStatus.COMPLETED, retried_at)

    async with factory() as uow:
        (stored,) = await ScheduleRunRepository(uow).list_for_schedule(schedule_id)
    assert stored.status is ScheduleRunStatus.COMPLETED
    assert stored.completed_at == retried_at.replace(tzinfo=timezone.utc)


async def test_only_a_later_different_workflow_outcome_replaces_a_failure(
    authenticated_client, fixed_test_org, db_manager
) -> None:
    """A redelivered or out-of-order report must not flip a recorded outcome."""
    _, schedule_id, user_id = await _workflow_schedule(
        authenticated_client, fixed_test_org["id"]
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    run = await _dispatched(
        factory, schedule_id=schedule_id, user_id=user_id, key="fire", kind="WORKFLOW"
    )
    failed_at = _BASE + timedelta(minutes=10)
    assert await _outcome(factory, run, ScheduleRunStatus.TARGET_FAILED, failed_at)

    earlier = failed_at - timedelta(minutes=1)
    assert not await _outcome(factory, run, ScheduleRunStatus.COMPLETED, earlier)
    assert not await _outcome(factory, run, ScheduleRunStatus.COMPLETED, None)
    later = failed_at + timedelta(minutes=1)
    # A retry that failed again leaves the failure as it was.
    assert not await _outcome(factory, run, ScheduleRunStatus.TARGET_FAILED, later)

    # An agent conversation is never retried under the same id.
    agent_run = await _dispatched(
        factory, schedule_id=schedule_id, user_id=user_id, key="agent", kind="AGENT"
    )
    assert await _outcome(
        factory, agent_run, ScheduleRunStatus.TARGET_FAILED, failed_at, kind="AGENT"
    )
    assert not await _outcome(
        factory, agent_run, ScheduleRunStatus.COMPLETED, later, kind="AGENT"
    )

    async with factory() as uow:
        runs = await ScheduleRunRepository(uow).list_for_schedule(schedule_id)
    assert [run.status for run in runs] == [
        ScheduleRunStatus.TARGET_FAILED,
        ScheduleRunStatus.TARGET_FAILED,
    ]


async def test_a_retry_success_does_not_reactivate_a_tripped_schedule(
    authenticated_client, fixed_test_org, db_manager
) -> None:
    """The breaker recounts the streak, and only ever deactivates."""
    threshold = schedule_settings.schedule_max_consecutive_failures
    assert threshold > 0
    _, schedule_id, user_id = await _workflow_schedule(
        authenticated_client, fixed_test_org["id"]
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    runs = []
    for index in range(threshold):
        run = await _dispatched(
            factory,
            schedule_id=schedule_id,
            user_id=user_id,
            key=f"fire-{index}",
            kind="WORKFLOW",
        )
        failed_at = _BASE + timedelta(minutes=index)
        assert await _outcome(factory, run, ScheduleRunStatus.TARGET_FAILED, failed_at)
        runs.append(run)
    tripped = await _schedule_row(db_manager, schedule_id)
    assert tripped.is_active is False
    assert tripped.consecutive_failures == threshold

    retried_at = _BASE + timedelta(hours=1)
    assert await _outcome(factory, runs[-1], ScheduleRunStatus.COMPLETED, retried_at)

    row = await _schedule_row(db_manager, schedule_id)
    assert row.consecutive_failures == 0
    assert row.is_active is False


async def test_resuming_a_tripped_schedule_forgives_the_streak_that_paused_it(
    authenticated_client, fixed_test_org, db_manager
) -> None:
    """One failure after a resume is one failure, not threshold + 1.

    Reactivating zeroed the counter, but the breaker recounts the streak from
    the run ledger on every outcome, and the ledger still held the failures
    that paused it -- so the next failure switched the schedule straight off.
    """
    threshold = schedule_settings.schedule_max_consecutive_failures
    assert threshold > 0
    pod_id, schedule_id, user_id = await _workflow_schedule(
        authenticated_client, fixed_test_org["id"]
    )
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    for index in range(threshold):
        run = await _dispatched(
            factory,
            schedule_id=schedule_id,
            user_id=user_id,
            key=f"fire-{index}",
            kind="WORKFLOW",
        )
        failed_at = _BASE + timedelta(minutes=index)
        assert await _outcome(factory, run, ScheduleRunStatus.TARGET_FAILED, failed_at)
    assert (await _schedule_row(db_manager, schedule_id)).is_active is False

    resumed = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule_id}", json={"is_active": True}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["consecutive_failures"] == 0

    run = await _dispatched(
        factory,
        schedule_id=schedule_id,
        user_id=user_id,
        key="after-resume",
        kind="WORKFLOW",
    )
    assert await _outcome(
        factory,
        run,
        ScheduleRunStatus.TARGET_FAILED,
        datetime.now(timezone.utc) + timedelta(seconds=1),
    )

    row = await _schedule_row(db_manager, schedule_id)
    assert row.consecutive_failures == 1
    assert row.is_active is True
