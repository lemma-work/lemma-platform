from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.identity.contracts.profiles import UserProfileRef
from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome
from app.modules.schedule.handlers import (
    schedule_lifecycle_consumer,
    schedule_notification_consumer,
    target_outcome_consumer,
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
    # Repositories bind a session in __init__; no test here issues a query
    # through it, so a placeholder is enough.
    session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _UoWFactory:
    def __call__(self):
        return _UoW()


def _ledger(record: AsyncMock):
    """A stand-in for the schedule ledger, passed in rather than patched on.

    The consumer takes its ledger and its conversation reader through
    ``Depends`` precisely so these tests never reach into the module they are
    testing: a patch there would keep passing after the name behind it changed.
    """
    return lambda uow: SimpleNamespace(record_target_outcome=record)


@pytest.mark.asyncio
async def test_workflow_terminal_event_records_failed_target() -> None:
    record = AsyncMock(return_value=True)
    run_id = uuid4()
    completed_at = datetime.now(timezone.utc)
    event = WorkflowRunTerminalEvent(
        run_id=run_id,
        status=WorkflowRunStatus.FAILED,
        error=None,
        completed_at=completed_at,
    ).model_dump(mode="json")

    await target_outcome_consumer.on_workflow_run_terminal(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
        ledger=_ledger(record),
    )

    record.assert_awaited_once_with(
        target_kind="WORKFLOW",
        target_run_id=str(run_id),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=completed_at,
        error_type="WorkflowRunFailed",
    )


@pytest.mark.asyncio
async def test_agent_outcome_uses_authoritative_conversation_status() -> None:
    """The agent event says a *run* finished; the ledger settles *conversations*.

    So the consumer asks `agent.contracts.conversation_outcomes` rather than
    trusting the event's own status, and the answer it gets back is the schedule
    module's own `TargetRunOutcome` -- the same projection the recovery sweep
    reconciles against.
    """
    completed_at = datetime.now(timezone.utc)
    resolve = AsyncMock(
        return_value=TargetRunOutcome(status="FAILED", ended_at=completed_at)
    )
    record = AsyncMock(return_value=True)
    conversation_id = uuid4()
    event = AgentRunCompletedEvent(
        conversation_id=conversation_id,
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    ).model_dump(mode="json")

    await target_outcome_consumer.on_agent_run_completed(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
        ledger=_ledger(record),
        conversation_outcome=resolve,
    )

    record.assert_awaited_once_with(
        target_kind="AGENT",
        target_run_id=str(conversation_id),
        status=ScheduleRunStatus.TARGET_FAILED,
        completed_at=completed_at,
        error_type="AgentConversationFailed",
    )


@pytest.mark.asyncio
async def test_agent_waiting_conversation_remains_dispatched() -> None:
    resolve = AsyncMock(
        return_value=TargetRunOutcome(
            status="WAITING", ended_at=datetime.now(timezone.utc)
        )
    )
    record = AsyncMock(return_value=True)
    event = AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    ).model_dump(mode="json")

    await target_outcome_consumer.on_agent_run_completed(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
        ledger=_ledger(record),
        conversation_outcome=resolve,
    )

    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_conversation_with_no_status_yet_is_left_for_the_sweep() -> None:
    """An absent status is not a terminal one, and must not settle the run.

    `TargetRunOutcome.status` is nullable because the column is: a conversation
    that has not been written a status yet is still in flight. The reader this
    replaced returned an entity whose status fell back to the latest *run*, so
    an in-flight conversation could read as finished here and as running in the
    recovery sweep.
    """
    record = AsyncMock(return_value=True)
    event = AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    ).model_dump(mode="json")

    await target_outcome_consumer.on_agent_run_completed(
        event,
        _Logger(),
        uow_factory=_UoWFactory(),
        inbox=PassthroughEventInbox(),
        ledger=_ledger(record),
        conversation_outcome=AsyncMock(
            return_value=TargetRunOutcome(status=None, ended_at=None)
        ),
    )

    record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule_type", [ScheduleType.TIME, ScheduleType.DATASTORE])
async def test_deactivation_needs_no_job_teardown(schedule_type) -> None:
    """Deactivation is self-enforcing now, and this asserts it stays that way.

    These two used to assert the consumer called `remove_job` on the scheduler
    sidecar for TIME schedules and skipped it for the rest. There is no sidecar
    and no job: the row *is* the timer, and the claim query filters on
    `is_active`, so the same UPDATE that deactivates a schedule is what stops it
    firing -- atomically, in one transaction, with no second system to keep in
    step.

    What is left to check here is that the consumer stays a no-op. If someone
    reintroduces an out-of-band teardown call, it belongs in the deactivating
    transaction rather than in an event handler that can arrive late or twice.
    The property that a deactivated schedule is never claimed is asserted
    against real Postgres in
    `tests/e2e/test_due_schedule_claimer_e2e.py::test_a_deactivated_schedule_is_never_claimed`.
    """
    event = ScheduleDeactivated(
        schedule_id=uuid4(),
        user_id=uuid4(),
        schedule_type=schedule_type,
        consecutive_failures=5,
    ).model_dump(mode="json")

    await schedule_lifecycle_consumer.on_schedule_deactivated(
        event,
        _Logger(),
        inbox=PassthroughEventInbox(),
    )


@pytest.mark.asyncio
async def test_deactivation_email_is_sent_to_schedule_owner(monkeypatch) -> None:
    owner_email = "schedule-owner@example.com"
    resolve_profile = AsyncMock(return_value=UserProfileRef(email=owner_email))
    send_email = AsyncMock()
    monkeypatch.setattr(
        "app.modules.identity.contracts.profiles.user_profile",
        resolve_profile,
    )
    monkeypatch.setattr(
        "app.core.email.email_sender.EmailSender.from_settings",
        lambda: SimpleNamespace(send_email=send_email),
    )
    # The consumer reads the schedule to build the review URL. Stub the
    # repository rather than a session: going through the ORM would make this
    # test depend on which model modules happen to be imported first.
    monkeypatch.setattr(
        "app.modules.schedule.repositories.schedule_repository.ScheduleRepository.get",
        AsyncMock(return_value=None),
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

    resolve_profile.assert_awaited_once()
    assert resolve_profile.await_args.args[1] == owner_id
    send_email.assert_awaited_once()
    assert send_email.await_args.kwargs["to_email"] == owner_email


@pytest.mark.asyncio
async def test_agent_and_workflow_cancellation_spellings_map_to_one_status() -> None:
    """Workflow runs say CANCELLED, agent conversations say STOPPED."""
    resolve = target_outcome_consumer._schedule_status_for

    assert resolve("CANCELLED") is ScheduleRunStatus.CANCELLED
    assert resolve("STOPPED") is ScheduleRunStatus.CANCELLED
    assert resolve("COMPLETED") is ScheduleRunStatus.COMPLETED
    assert resolve("FAILED") is ScheduleRunStatus.TARGET_FAILED
    assert resolve("RUNNING") is None


def test_every_terminal_workflow_status_maps_onto_the_ledger() -> None:
    """Adding a terminal workflow status must not silently orphan schedule runs.

    ``_collect_terminal_event`` publishes exactly the statuses in
    ``TERMINAL_STATUSES``. If one gains a new member without a ledger mapping,
    schedule runs targeting it would sit in DISPATCHED forever, so fail here
    instead — at the point where the mapping is defined.
    """
    from app.modules.workflow.domain.run import TERMINAL_STATUSES

    unmapped = sorted(
        status.value
        for status in TERMINAL_STATUSES
        if target_outcome_consumer._schedule_status_for(status.value) is None
    )
    assert not unmapped, f"terminal workflow statuses with no ledger status: {unmapped}"
