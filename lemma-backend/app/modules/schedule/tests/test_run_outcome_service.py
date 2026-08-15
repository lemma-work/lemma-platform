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
    service.run_repository = Mock(
        transition_target_outcome=AsyncMock(),
        consecutive_terminal_failures=AsyncMock(),
    )
    service.schedule_repository = Mock(
        get_for_update=AsyncMock(),
        increment_consecutive_failures=AsyncMock(),
        reset_consecutive_failures=AsyncMock(),
        set_consecutive_failures=AsyncMock(),
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
    service.run_repository.consecutive_terminal_failures.return_value = 5
    service.schedule_repository.deactivate_if_active.return_value = True

    changed = await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=None,
        error_type="WorkflowRunFailed",
    )

    assert changed is True
    service.run_repository.consecutive_terminal_failures.assert_awaited_once_with(
        schedule.id
    )
    service.schedule_repository.set_consecutive_failures.assert_awaited_once_with(
        schedule.id, 5
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
    service.run_repository.consecutive_terminal_failures.return_value = 0

    changed = await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=status,
        completed_at=None,
    )

    assert changed is True
    service.schedule_repository.set_consecutive_failures.assert_awaited_once_with(
        schedule.id, 0
    )
    uow.collect_events.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_dead_letter_counts_without_target_outcome():
    service, _ = _service()
    schedule = _schedule()
    service.schedule_repository.get_for_update.return_value = schedule
    service.run_repository.consecutive_terminal_failures.return_value = 2

    await service.record_dispatch_dead_letter(schedule)

    service.run_repository.consecutive_terminal_failures.assert_awaited_once_with(
        schedule.id
    )
    service.schedule_repository.set_consecutive_failures.assert_awaited_once_with(
        schedule.id, 2
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


@pytest.mark.anyio
async def test_a_fire_that_never_dispatched_still_counts_against_the_breaker():
    """Quota exhaustion has to reach the breaker, or nothing ever stops it.

    The ledger is written at dispatch. A fire whose LLM filter could not run —
    the pod is out of agent-run budget — never gets that far, so no row existed,
    and the breaker counts rows. Production showed one pod producing thirty of
    these in a day with no ceiling and nothing told to the owner.
    """
    service, _ = _service()
    schedule = _schedule()
    run_id = uuid4()
    service.run_repository.claim = AsyncMock(
        return_value=SimpleNamespace(id=run_id, schedule_id=schedule.id)
    )
    service.run_repository.dead_letter = AsyncMock(return_value=True)
    service.schedule_repository.get_for_update.return_value = schedule
    service.run_repository.consecutive_terminal_failures.return_value = 1

    recorded = await service.record_pre_dispatch_failure(
        schedule,
        source_event_id="webhook-42",
        error_type="ScheduleFilterQuotaExhausted",
    )

    assert recorded is True
    # DEAD_LETTERED, not FAILED: `consecutive_terminal_failures` deliberately
    # ignores FAILED as a retryable intermediate state, so a failure recorded
    # that way would never reach the threshold.
    service.run_repository.dead_letter.assert_awaited_once_with(
        run_id, error_type="ScheduleFilterQuotaExhausted"
    )
    service.schedule_repository.set_consecutive_failures.assert_awaited_once_with(
        schedule.id, 1
    )


@pytest.mark.anyio
async def test_the_fifth_quota_failure_deactivates_the_schedule(monkeypatch):
    """The ceiling the whole mechanism exists for: five, then stop."""
    monkeypatch.setattr(
        "app.modules.schedule.services.run_outcome_service.schedule_settings.schedule_max_consecutive_failures",
        5,
    )
    service, uow = _service()
    schedule = _schedule()
    service.run_repository.claim = AsyncMock(
        return_value=SimpleNamespace(id=uuid4(), schedule_id=schedule.id)
    )
    service.run_repository.dead_letter = AsyncMock(return_value=True)
    service.schedule_repository.get_for_update.return_value = schedule
    service.run_repository.consecutive_terminal_failures.return_value = 5
    service.schedule_repository.deactivate_if_active.return_value = True

    await service.record_pre_dispatch_failure(
        schedule, source_event_id="webhook-5", error_type="ScheduleFilterQuotaExhausted"
    )

    service.schedule_repository.deactivate_if_active.assert_awaited_once_with(
        schedule.id
    )
    collected = uow.collect_events.call_args[0][0]
    assert any(isinstance(event, ScheduleDeactivated) for event in collected), (
        "the owner is emailed off ScheduleDeactivated; without it the schedule "
        "stops silently"
    )


@pytest.mark.anyio
async def test_a_repeated_source_event_does_not_inflate_the_streak():
    """A redelivery of the same fire must not count twice.

    `claim` returns None for an event already recorded, which is what keeps the
    breaker counting distinct fires rather than delivery attempts.
    """
    service, _ = _service()
    schedule = _schedule()
    service.run_repository.claim = AsyncMock(return_value=None)
    service.run_repository.dead_letter = AsyncMock()

    recorded = await service.record_pre_dispatch_failure(
        schedule, source_event_id="webhook-42", error_type="ScheduleFilterQuotaExhausted"
    )

    assert recorded is False
    service.run_repository.dead_letter.assert_not_awaited()
