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
        status = {
            "COMPLETED": ScheduleRunStatus.COMPLETED,
            "FAILED": ScheduleRunStatus.TARGET_FAILED,
            "CANCELLED": ScheduleRunStatus.CANCELLED,
        }[parsed.status.value]
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
            status = _agent_schedule_status(outcome.status)
            if status is None:
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


def _agent_schedule_status(
    status: str,
) -> ScheduleRunStatus | None:
    return {
        "COMPLETED": ScheduleRunStatus.COMPLETED,
        "FAILED": ScheduleRunStatus.TARGET_FAILED,
        "STOPPED": ScheduleRunStatus.CANCELLED,
    }.get(status)
