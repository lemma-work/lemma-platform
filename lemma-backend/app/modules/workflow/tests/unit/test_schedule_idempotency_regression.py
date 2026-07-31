"""Regression tests for the workflow+schedule idempotency fixes.

Covers:
- C1 (LP-057): start_run persists the run row BEFORE advancing, so the
  schedule-event unique constraint gates node side effects.
- C3 (LP-102): a duplicate agent-target schedule fire is skipped via the durable
  PostgreSQL ledger, so the agent conversation is not started twice.
- E: the failure circuit breaker counts distinct dead-lettered schedule runs,
  ignores delivery attempts, resets on successful dispatch, and deactivates at
  the threshold.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import (
    ScheduleFireStatus,
    ScheduleRunStatus,
    ScheduleType,
)
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.services.schedule_start_service import ScheduleStartService


def _engine_with_mocks() -> WorkflowEngine:
    uow = Mock()
    uow.commit = AsyncMock()
    uow.session = Mock()
    engine = WorkflowEngine(
        uow,
        agent_adapter=Mock(),
        function_adapter=Mock(),
        schedule_adapter=Mock(),
    )
    return engine


@pytest.mark.anyio
async def test_start_run_persists_row_before_advancing():
    """The run row must be created/flushed (under the unique constraint) before
    stepper.advance runs any node side effects (LP-057)."""
    engine = _engine_with_mocks()

    flow_id, pod_id, user_id = uuid4(), uuid4(), uuid4()
    engine.flow_repo.get = AsyncMock(
        return_value=SimpleNamespace(id=flow_id, pod_id=pod_id)
    )
    engine._require_action = AsyncMock(return_value=None)
    engine._entry_node_id = Mock(return_value="entry")

    engine.run_repo = AsyncMock()
    engine.run_repo.update.return_value = SimpleNamespace(wait=None)

    stepper = Mock()
    stepper.advance = AsyncMock(return_value=SimpleNamespace(wait=None))
    engine._stepper = Mock(return_value=stepper)

    # Record the relative order of create vs advance.
    order = Mock()
    order.attach_mock(engine.run_repo.create, "create")
    order.attach_mock(stepper.advance, "advance")

    await engine.start_run(flow_id, user_id)

    engine.run_repo.create.assert_awaited_once()
    stepper.advance.assert_awaited_once()
    call_names = [c[0] for c in order.mock_calls]
    assert call_names.index("create") < call_names.index("advance"), (
        "run row must be persisted before node side effects run"
    )


@pytest.mark.anyio
async def test_duplicate_agent_schedule_fire_is_skipped(monkeypatch):
    """A redelivered agent-target fire whose ledger claim fails must not start a
    second conversation (LP-102)."""
    engine = _engine_with_mocks()
    engine.agent_adapter.run_agent_by_id = AsyncMock(return_value=uuid4())

    schedule = SimpleNamespace(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        workflow_id=None,
        agent_id=uuid4(),
        is_active=True,
        schedule_type=SimpleNamespace(value="TIME"),
    )

    svc = ScheduleStartService(engine)

    # Schedule lookup returns our agent-target schedule.
    import app.modules.workflow.services.schedule_start_service as repo_mod

    monkeypatch.setattr(
        repo_mod,
        "ScheduleRepository",
        lambda uow: Mock(get=AsyncMock(return_value=schedule)),
    )

    # The durable dedup claim reports "already delivered".
    import app.modules.workflow.services.schedule_start_service as run_repo_mod

    run_repo = Mock()
    run_repo.claim = AsyncMock(return_value=None)
    monkeypatch.setattr(run_repo_mod, "ScheduleRunRepository", lambda uow: run_repo)

    await svc.handle_schedule_fired(
        schedule_id=str(schedule.id),
        payload={},
        schedule_event_id="evt-1",
    )

    run_repo.claim.assert_awaited_once()
    engine.agent_adapter.run_agent_by_id.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "run_status,count,expect_deactivate",
    [
        (ScheduleRunStatus.DISPATCHED, None, False),
        (ScheduleRunStatus.FAILED, None, False),
        (ScheduleRunStatus.DEAD_LETTERED, 2, False),
        (ScheduleRunStatus.DEAD_LETTERED, 3, True),
    ],
)
async def test_failure_circuit_breaker(
    monkeypatch,
    run_status,
    count,
    expect_deactivate,
):
    """Only distinct terminal runs contribute to the breaker streak."""
    monkeypatch.setattr(
        "app.modules.schedule.config.schedule_settings.schedule_max_consecutive_failures",
        3,
    )

    svc = ScheduleStartService(_engine_with_mocks())
    schedule = SimpleNamespace(id=uuid4(), user_id=uuid4(), schedule_type="TIME")
    schedule_repo = Mock()
    schedule_repo.update = AsyncMock()
    schedule_repo.set_consecutive_failures = AsyncMock()
    run_repo = Mock()
    run_repo.consecutive_terminal_failures = AsyncMock(return_value=count)

    tripped = await svc._apply_failure_policy(
        schedule_repo,
        run_repo,
        schedule,
        run_status,
    )

    if run_status == ScheduleRunStatus.DISPATCHED:
        schedule_repo.set_consecutive_failures.assert_awaited_once_with(schedule.id, 0)
        run_repo.consecutive_terminal_failures.assert_not_awaited()
        assert tripped is None
    elif run_status == ScheduleRunStatus.FAILED:
        schedule_repo.set_consecutive_failures.assert_not_awaited()
        run_repo.consecutive_terminal_failures.assert_not_awaited()
        assert tripped is None
    elif expect_deactivate:
        schedule_repo.set_consecutive_failures.assert_awaited_once_with(
            schedule.id, count
        )
        schedule_repo.update.assert_awaited_once_with(schedule.id, is_active=False)
        assert tripped == count
    else:
        schedule_repo.set_consecutive_failures.assert_awaited_once_with(
            schedule.id, count
        )
        schedule_repo.update.assert_not_awaited()
        assert tripped is None


@pytest.mark.anyio
async def test_deactivation_event_is_staged_before_fire_transaction_commits(
    monkeypatch,
):
    engine = _engine_with_mocks()
    service = ScheduleStartService(engine)
    schedule = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.TIME,
    )
    schedule_repo = Mock()
    schedule_repo.record_fire = AsyncMock()
    run_repo = Mock()
    monkeypatch.setattr(service, "_apply_failure_policy", AsyncMock(return_value=3))

    await service._record_fire(
        schedule_repo,
        run_repo,
        schedule,
        status=ScheduleFireStatus.ERROR,
        error="target dispatch failed",
        run_status=ScheduleRunStatus.DEAD_LETTERED,
    )

    staged = engine.uow.collect_events.call_args.args[0]
    assert len(staged) == 1
    assert isinstance(staged[0], ScheduleDeactivated)
    assert staged[0].schedule_id == schedule.id
    assert staged[0].consecutive_failures == 3
    engine.uow.commit.assert_awaited_once()
