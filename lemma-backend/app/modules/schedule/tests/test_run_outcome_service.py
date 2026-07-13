from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleRunStatus,
    ScheduleType,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)


def _service() -> tuple[ScheduleRunOutcomeService, Mock]:
    uow = Mock(session=Mock(), collect_events=Mock())
    service = ScheduleRunOutcomeService(uow)
    service.run_repository = Mock(transition_target_outcome=AsyncMock())
    service.schedule_repository = Mock(
        get_for_update=AsyncMock(),
        increment_consecutive_failures=AsyncMock(),
        reset_consecutive_failures=AsyncMock(),
        deactivate_if_active=AsyncMock(return_value=False),
        lock_breaker_candidates=AsyncMock(return_value=[]),
    )
    return service, uow


def _schedule() -> ScheduleEntity:
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        workflow_id=uuid4(),
        config={"table_name": "rows", "operations": ["INSERT"]},
    )


@pytest.mark.anyio
async def test_fifth_target_failure_deactivates_and_notifies_schedule_owner(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.schedule.services.run_outcome_service.schedule_settings.schedule_max_consecutive_failures",
        5,
    )
    service, uow = _service()
    schedule = _schedule()
    service.run_repository.transition_target_outcome.return_value = SimpleNamespace(
        schedule_id=schedule.id
    )
    service.schedule_repository.get_for_update.return_value = schedule
    service.schedule_repository.increment_consecutive_failures.return_value = 5
    service.schedule_repository.deactivate_if_active.return_value = True

    changed = await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=None,
        error_type="WorkflowRunFailed",
    )

    assert changed is True
    service.schedule_repository.increment_consecutive_failures.assert_awaited_once_with(
        schedule.id
    )
    staged = uow.collect_events.call_args.args[0]
    assert len(staged) == 1
    assert isinstance(staged[0], ScheduleDeactivated)
    assert staged[0].user_id == schedule.user_id
    assert staged[0].consecutive_failures == 5


@pytest.mark.anyio
async def test_duplicate_target_outcome_is_a_noop():
    service, uow = _service()
    service.run_repository.transition_target_outcome.return_value = None

    changed = await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=None,
    )

    assert changed is False
    service.schedule_repository.get_for_update.assert_not_awaited()
    uow.collect_events.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status",
    [ScheduleRunStatus.COMPLETED, ScheduleRunStatus.CANCELLED],
)
async def test_success_and_cancellation_reset_the_failure_streak(status):
    service, uow = _service()
    schedule = _schedule()
    service.run_repository.transition_target_outcome.return_value = SimpleNamespace(
        schedule_id=schedule.id
    )
    service.schedule_repository.get_for_update.return_value = schedule

    changed = await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=status,
        completed_at=None,
    )

    assert changed is True
    service.schedule_repository.reset_consecutive_failures.assert_awaited_once_with(
        schedule.id
    )
    service.schedule_repository.increment_consecutive_failures.assert_not_awaited()
    uow.collect_events.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_dead_letter_counts_without_target_outcome():
    service, _ = _service()
    schedule = _schedule()
    service.schedule_repository.get_for_update.return_value = schedule
    service.schedule_repository.increment_consecutive_failures.return_value = 2

    await service.record_dispatch_dead_letter(schedule)

    service.schedule_repository.increment_consecutive_failures.assert_awaited_once_with(
        schedule.id
    )
    service.run_repository.transition_target_outcome.assert_not_awaited()


@pytest.mark.anyio
async def test_startup_reconciliation_deactivates_backfilled_candidates_once(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.schedule.services.run_outcome_service.schedule_settings.schedule_max_consecutive_failures",
        5,
    )
    service, uow = _service()
    first = _schedule()
    first.consecutive_failures = 7
    second = _schedule()
    second.consecutive_failures = 5
    service.schedule_repository.lock_breaker_candidates.return_value = [first, second]
    service.schedule_repository.deactivate_if_active.side_effect = [True, False]

    count = await service.reconcile_tripped_schedules()

    assert count == 1
    service.schedule_repository.lock_breaker_candidates.assert_awaited_once_with(5)
    assert service.schedule_repository.deactivate_if_active.await_count == 2
    staged = uow.collect_events.call_args.args[0]
    assert len(staged) == 1
    assert staged[0].schedule_id == first.id
    assert staged[0].user_id == first.user_id
