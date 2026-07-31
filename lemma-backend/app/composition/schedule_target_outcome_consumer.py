"""Bridge workflow and agent terminal outcomes into the schedule ledger."""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.composition.schedule_target_outcomes import (
    resolve_agent_conversation_outcome,
)
from app.core.infrastructure.db.session import async_session_maker
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


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    WORKFLOW_RUN_EVENTS_STREAM,
    group="schedule-workflow-outcomes",
    consumer="schedule-workflow-outcomes-consumer",
)
async def on_workflow_run_terminal(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
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
            changed = await ScheduleRunOutcomeService(uow).record_target_outcome(
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
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != AgentRunCompletedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = AgentRunCompletedEvent.model_validate(event)
        async with uow_factory() as uow:
            outcome = await resolve_agent_conversation_outcome(
                uow, parsed.conversation_id
            )
            if outcome is None:
                return
            status = _schedule_status_for(outcome.status)
            if status is None:
                # Agent conversations reach non-terminal states this consumer is
                # not interested in, so this is the common path, not an anomaly.
                return
            changed = await ScheduleRunOutcomeService(uow).record_target_outcome(
                target_kind="AGENT",
                target_run_id=str(parsed.conversation_id),
                status=status,
                completed_at=outcome.completed_at,
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
