"""Bridge workflow and agent terminal outcomes into the schedule ledger.

Schedule's own handler, not a composition adapter. It reads two other modules
through what they publish -- their `domain/events` for the notification, and
`agent.contracts.conversation_outcomes` for the one fact an agent event does
not carry -- and everything it writes is this module's.

Both consumer groups are declared in `schedule/module.py`'s `stream_groups`. An
undeclared group is created on its first read and silently misses everything
published before that.

The two collaborators arrive through `Depends` rather than being resolved in
the body, for the same reason `uow_factory` and `inbox` already do: a subscriber
whose collaborators are reachable only by name can be tested only by patching
its own module, which certifies the half the test did not write.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.log.log import get_logger
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
)
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.workflow.domain.events import (
    WORKFLOW_RUN_EVENTS_STREAM,
    WorkflowRunTerminalEvent,
)

router = RedisRouter()
logger = get_logger(__name__)


class ScheduleLedger(Protocol):
    """The one write this consumer makes, named so a test can stand in for it."""

    async def record_target_outcome(
        self,
        *,
        target_kind: str,
        target_run_id: str,
        status: ScheduleRunStatus,
        completed_at: datetime | None,
        error_type: str | None = None,
    ) -> bool: ...


#: Bound to a transaction, because recording an outcome updates the run *and*
#: its schedule's failure streak and the two must not be able to disagree.
LedgerFactory = Callable[[SqlAlchemyUnitOfWork], ScheduleLedger]

#: `agent.contracts.conversation_outcomes.load_conversation_outcome`. An agent
#: run completing is not the same event as its conversation finishing -- a
#: conversation outlives the runs inside it -- so the ledger has to ask.
ConversationOutcomeReader = Callable[
    [SqlAlchemyUnitOfWork, UUID], Awaitable[TargetRunOutcome | None]
]


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def provide_ledger() -> LedgerFactory:
    return ScheduleRunOutcomeService


def provide_conversation_outcome() -> ConversationOutcomeReader:
    from app.modules.agent.contracts.conversation_outcomes import (
        load_conversation_outcome,
    )

    return load_conversation_outcome


@reliable_redis_stream_subscriber(
    router,
    WORKFLOW_RUN_EVENTS_STREAM,
    group="schedule-workflow-outcomes",
    consumer="schedule-workflow-outcomes-consumer",
)
async def on_workflow_run_terminal(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
    ledger: LedgerFactory = Depends(provide_ledger),
) -> None:
    if event.get("event_type") != WorkflowRunTerminalEvent.get_event_type():
        return

    async def record() -> None:
        parsed = WorkflowRunTerminalEvent.model_validate(event)
        status = _schedule_status_for(parsed.status.value)
        if status is None:
            _log_unmapped_target_outcome("WORKFLOW", parsed.status.value)
            return
        async with uow_factory() as uow:
            changed = await ledger(uow).record_target_outcome(
                target_kind="WORKFLOW",
                target_run_id=str(parsed.run_id),
                status=status,
                completed_at=parsed.completed_at,
                error_type=(
                    "WorkflowRunFailed"
                    if status == ScheduleRunStatus.TARGET_FAILED
                    else None
                ),
            )
        if changed:
            fs_logger.debug(
                "schedule.workflow_outcome.recorded",
                run_id=str(parsed.run_id),
            )

    await inbox.process("schedule.workflow-outcomes", event, record)


@reliable_redis_stream_subscriber(
    router,
    AGENT_EVENTS_STREAM,
    group="schedule-agent-outcomes",
    consumer="schedule-agent-outcomes-consumer",
)
async def on_agent_run_completed(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
    ledger: LedgerFactory = Depends(provide_ledger),
    conversation_outcome: ConversationOutcomeReader = Depends(
        provide_conversation_outcome
    ),
) -> None:
    if event.get("event_type") != AgentRunCompletedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = AgentRunCompletedEvent.model_validate(event)
        async with uow_factory() as uow:
            outcome = await conversation_outcome(uow, parsed.conversation_id)
            if outcome is None or outcome.status is None:
                return
            status = _schedule_status_for(outcome.status)
            if status is None:
                # Agent conversations reach non-terminal states this consumer is
                # not interested in, so this is the common path, not an anomaly.
                return
            changed = await ledger(uow).record_target_outcome(
                target_kind="AGENT",
                target_run_id=str(parsed.conversation_id),
                status=status,
                completed_at=outcome.ended_at,
                error_type=(
                    "AgentConversationFailed"
                    if status == ScheduleRunStatus.TARGET_FAILED
                    else None
                ),
            )
        if changed:
            fs_logger.debug(
                "schedule.agent_outcome.recorded",
                conversation_id=str(parsed.conversation_id),
            )

    await inbox.process("schedule.agent-outcomes", event, record)


def _schedule_status_for(status: str) -> ScheduleRunStatus | None:
    """Map a target's terminal state onto the ledger, or None if it has none.

    Workflow runs and agent conversations spell the cancelled state differently
    ("CANCELLED" vs "STOPPED") but otherwise agree, so one table serves both.
    Returning None rather than raising keeps an unrecognised state from turning
    a consumer into a redelivery loop.
    """
    return {
        "COMPLETED": ScheduleRunStatus.COMPLETED,
        "FAILED": ScheduleRunStatus.TARGET_FAILED,
        "CANCELLED": ScheduleRunStatus.CANCELLED,
        "STOPPED": ScheduleRunStatus.CANCELLED,
    }.get(status)


def _log_unmapped_target_outcome(target_kind: str, status: str) -> None:
    """A workflow run only publishes terminal states, so this means drift."""
    logger.error(
        "schedule.target_outcome.unmapped",
        target_kind=target_kind,
        target_status=status,
    )
