from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleRunCompleted,
)
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
    service.work = Mock(add=AsyncMock())
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
    # The owner's email is a delivery item written in this same transaction.
    [notice] = service.work.add.await_args_list
    assert notice.args[0] == "schedule.paused_notice"
    assert notice.args[2].reason == "consecutive_failures"


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
    # A healthy outcome must not trip the breaker. It does now record the run's
    # completion, so this asserts the absence of the deactivation specifically
    # rather than the absence of every event.
    collected = [
        event for call in uow.collect_events.call_args_list for event in call.args[0]
    ]
    assert not [e for e in collected if isinstance(e, ScheduleDeactivated)]
    assert [e for e in collected if isinstance(e, ScheduleRunCompleted)]


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
        schedule,
        source_event_id="webhook-42",
        error_type="ScheduleFilterQuotaExhausted",
    )

    assert recorded is False
    service.run_repository.dead_letter.assert_not_awaited()


# --- lemma.schedule.* metrics -------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.modules.test_support.metric_capture import (  # noqa: E402
    metric_reader,
    total,
)


def _metric_delta(name: str, **labels: object):
    metric_reader()
    before = total(name, **labels)
    return lambda: total(name, **labels) - before


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (ScheduleRunStatus.COMPLETED, "completed"),
        (ScheduleRunStatus.TARGET_FAILED, "failed"),
    ],
)
async def test_a_terminal_outcome_times_the_run(status, outcome):
    service, _ = _service()
    schedule = _schedule()
    started = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc)
    service.run_repository.transition_target_outcome.return_value = SimpleNamespace(
        schedule_id=schedule.id,
        started_at=started,
        completed_at=started + timedelta(seconds=42),
    )
    service.schedule_repository.get_for_update.return_value = schedule
    service.run_repository.consecutive_terminal_failures.return_value = 0
    timed = _metric_delta(
        "lemma.schedule.run.duration", schedule_type="datastore", outcome=outcome
    )

    await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=status,
        completed_at=None,
    )

    assert timed() == 1


@pytest.mark.anyio
async def test_a_filtered_event_is_counted_once_and_a_redelivery_not_at_all():
    service, _ = _service()
    service.run_repository.insert_filtered = AsyncMock(side_effect=[True, False])
    filtered = _metric_delta(
        "lemma.schedule.runs", schedule_type="datastore", outcome="filtered"
    )
    for _ in range(2):
        await service.record_filtered(
            _schedule(),
            source_event_id="e1",
            user_id=uuid4(),
            metadata=None,
            llm_output=None,
        )
    assert filtered() == 1


@pytest.mark.anyio
async def test_a_tripped_breaker_is_counted(monkeypatch):
    monkeypatch.setattr(
        "app.modules.schedule.services.run_outcome_service.schedule_settings.schedule_max_consecutive_failures",
        5,
    )
    service, _ = _service()
    schedule = _schedule()
    service.run_repository.consecutive_terminal_failures.return_value = 5
    service.schedule_repository.deactivate_if_active.return_value = True
    tripped = _metric_delta(
        "lemma.schedule.runs", schedule_type="datastore", outcome="breaker_tripped"
    )
    await service._apply_breaker(schedule)
    assert tripped() == 1


def test_fire_decisions_fold_onto_the_run_vocabulary():
    from app.modules.schedule.services.trigger_fire import FireOutcome, _counted

    schedule = _schedule().model_copy(update={"schedule_type": ScheduleType.TIME})
    triggered = _metric_delta(
        "lemma.schedule.runs", schedule_type="time", outcome="triggered"
    )
    skipped = _metric_delta(
        "lemma.schedule.runs", schedule_type="time", outcome="skipped"
    )
    for outcome in FireOutcome:
        assert _counted(schedule, outcome) is outcome
    assert triggered() == 1
    assert skipped() == 2  # the fire cap and the run cap


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("schedule_type", "origin"),
    [
        (ScheduleType.TIME, "SCHEDULE"),
        (ScheduleType.DATASTORE, "DATA_TRIGGER"),
        (ScheduleType.WEBHOOK, "CONNECTOR"),
    ],
)
async def test_a_run_outcome_names_what_fired_the_schedule(schedule_type, origin):
    """The outcome is recorded by a work item, which has no origin of its own to
    lend the event -- so without this it carried none, and the analytics
    projection, which only counts schedule-fired runs, never counted one."""
    service, uow = _service()
    schedule = _schedule().model_copy(update={"schedule_type": schedule_type})
    service.run_repository.transition_target_outcome.return_value = SimpleNamespace(
        schedule_id=schedule.id, redrive_of_run_id=None
    )
    service.schedule_repository.get_for_update.return_value = schedule
    service.run_repository.consecutive_terminal_failures.return_value = 0

    await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=ScheduleRunStatus.COMPLETED,
        completed_at=None,
    )

    (completed,) = [
        event
        for call in uow.collect_events.call_args_list
        for event in call.args[0]
        if isinstance(event, ScheduleRunCompleted)
    ]
    assert completed.origin == origin


@pytest.mark.anyio
async def test_a_redrive_outcome_claims_no_schedule_origin():
    """Somebody asked for that run again; the schedule's type does not say how."""
    service, uow = _service()
    schedule = _schedule()
    service.run_repository.transition_target_outcome.return_value = SimpleNamespace(
        schedule_id=schedule.id, redrive_of_run_id=uuid4()
    )
    service.schedule_repository.get_for_update.return_value = schedule
    service.run_repository.consecutive_terminal_failures.return_value = 0

    await service.record_target_outcome(
        target_kind="WORKFLOW",
        target_run_id=str(uuid4()),
        status=ScheduleRunStatus.COMPLETED,
        completed_at=None,
    )

    (completed,) = [
        event
        for call in uow.collect_events.call_args_list
        for event in call.args[0]
        if isinstance(event, ScheduleRunCompleted)
    ]
    assert completed.origin is None
