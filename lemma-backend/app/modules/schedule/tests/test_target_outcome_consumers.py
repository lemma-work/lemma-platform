from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.composition import schedule_target_outcome_consumer
from app.composition.schedule_target_outcomes import AgentConversationOutcome
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.handlers import (
    schedule_lifecycle_consumer,
    schedule_notification_consumer,
)
from app.modules.test_support.fakes import PassthroughEventInbox
from app.modules.workflow.domain.events import WorkflowRunTerminalEvent
from app.modules.workflow.domain.run import WorkflowRunStatus


class _Logger:
    def debug(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass


class _UoW:
    def __init__(self) -> None:
        # The deactivation consumer reads the schedule through ScheduleRepository
        # to build the review URL; no row is needed to assert the recipient.
        self.session = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _UoWFactory:
    def __call__(self):
        return _UoW()


@pytest.mark.asyncio
async def test_workflow_terminal_event_records_failed_target(monkeypatch) -> None:
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(
        schedule_target_outcome_consumer,
        "ScheduleRunOutcomeService",
        lambda uow: SimpleNamespace(record_target_outcome=record),
    )
    run_id = uuid4()
    completed_at = datetime.now(timezone.utc)
    event = WorkflowRunTerminalEvent(
        run_id=run_id,
        status=WorkflowRunStatus.FAILED,
        error=None,
        completed_at=completed_at,
    ).model_dump(mode="json")

    await schedule_target_outcome_consumer.on_workflow_run_terminal(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
    )

    record.assert_awaited_once_with(
        target_kind="WORKFLOW",
        target_run_id=str(run_id),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=completed_at,
        error_type="WorkflowRunFailed",
    )


@pytest.mark.asyncio
async def test_agent_outcome_uses_authoritative_conversation_status(
    monkeypatch,
) -> None:
    completed_at = datetime.now(timezone.utc)
    resolve = AsyncMock(
        return_value=AgentConversationOutcome(
            status="FAILED",
            completed_at=completed_at,
        )
    )
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(
        schedule_target_outcome_consumer,
        "resolve_agent_conversation_outcome",
        resolve,
    )
    monkeypatch.setattr(
        schedule_target_outcome_consumer,
        "ScheduleRunOutcomeService",
        lambda uow: SimpleNamespace(record_target_outcome=record),
    )
    conversation_id = uuid4()
    event = AgentRunCompletedEvent(
        conversation_id=conversation_id,
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    ).model_dump(mode="json")

    await schedule_target_outcome_consumer.on_agent_run_completed(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
    )

    record.assert_awaited_once_with(
        target_kind="AGENT",
        target_run_id=str(conversation_id),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=completed_at,
        error_type="AgentConversationFailed",
    )


@pytest.mark.asyncio
async def test_agent_waiting_conversation_remains_dispatched(monkeypatch) -> None:
    monkeypatch.setattr(
        schedule_target_outcome_consumer,
        "resolve_agent_conversation_outcome",
        AsyncMock(
            return_value=AgentConversationOutcome(
                status="WAITING",
                completed_at=datetime.now(timezone.utc),
            )
        ),
    )
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(
        schedule_target_outcome_consumer,
        "ScheduleRunOutcomeService",
        lambda uow: SimpleNamespace(record_target_outcome=record),
    )
    event = AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    ).model_dump(mode="json")

    await schedule_target_outcome_consumer.on_agent_run_completed(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
    )

    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_deactivated_time_schedule_removes_scheduler_job(monkeypatch) -> None:
    remove_job = AsyncMock()
    monkeypatch.setattr(
        schedule_lifecycle_consumer,
        "SchedulerAPIClient",
        lambda: SimpleNamespace(remove_job=remove_job),
    )
    schedule_id = uuid4()
    event = ScheduleDeactivated(
        schedule_id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        consecutive_failures=5,
    ).model_dump(mode="json")

    await schedule_lifecycle_consumer.on_schedule_deactivated(
        event,
        _Logger(),
        inbox=PassthroughEventInbox(),
    )

    remove_job.assert_awaited_once_with(schedule_id)


@pytest.mark.asyncio
async def test_non_time_deactivation_has_no_scheduler_job(monkeypatch) -> None:
    remove_job = AsyncMock()
    monkeypatch.setattr(
        schedule_lifecycle_consumer,
        "SchedulerAPIClient",
        lambda: SimpleNamespace(remove_job=remove_job),
    )
    event = ScheduleDeactivated(
        schedule_id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        consecutive_failures=5,
    ).model_dump(mode="json")

    await schedule_lifecycle_consumer.on_schedule_deactivated(
        event,
        _Logger(),
        inbox=PassthroughEventInbox(),
    )

    remove_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_deactivation_email_is_sent_to_schedule_owner(monkeypatch) -> None:
    owner_email = "schedule-owner@example.com"
    resolve_email = AsyncMock(return_value=owner_email)
    send_email = AsyncMock()
    monkeypatch.setattr(
        "app.composition.identity_notifications.resolve_user_email",
        resolve_email,
    )
    monkeypatch.setattr(
        "app.core.email.email_sender.EmailSender.from_settings",
        lambda: SimpleNamespace(send_email=send_email),
    )
    owner_id = uuid4()
    event = ScheduleDeactivated(
        schedule_id=uuid4(),
        user_id=owner_id,
        schedule_type=ScheduleType.DATASTORE,
        consecutive_failures=5,
    ).model_dump(mode="json")

    await schedule_notification_consumer.on_schedule_deactivated(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
    )

    resolve_email.assert_awaited_once()
    assert resolve_email.await_args.args[1] == owner_id
    send_email.assert_awaited_once()
    assert send_email.await_args.kwargs["to_email"] == owner_email
