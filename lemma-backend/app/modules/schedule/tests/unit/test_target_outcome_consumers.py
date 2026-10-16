from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome
from app.modules.schedule.handlers import target_outcome_consumer
from app.modules.test_support.fakes import PassthroughEventInbox


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
