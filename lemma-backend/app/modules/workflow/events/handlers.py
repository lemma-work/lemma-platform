"""Background job handlers and FastStream event consumers for Workflow module."""

import hashlib
import json
from datetime import datetime

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_cron,
    streaq_task,
    streaq_worker,
)

from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
)
from app.modules.function.domain.events import (
    FUNCTION_RUN_EVENTS_STREAM,
    FunctionRunCompletedEvent,
    FunctionRunFailedEvent,
)
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.modules.workflow.services.run_resume_service import RunResumeService
from app.modules.workflow.services.schedule_start_service import ScheduleStartService
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()


def provide_job_queue() -> SharedStreaqJobQueue:
    """Get the shared streaq job queue."""
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    FUNCTION_RUN_EVENTS_STREAM,
    group="workflow-function-events",
    consumer="workflow-function-events-consumer",
)
async def handle_function_run_event(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Handle function run events for workflow resumption."""
    event_type = event.get("event_type")

    if event_type not in {
        FunctionRunCompletedEvent.get_event_type(),
        FunctionRunFailedEvent.get_event_type(),
    }:
        return

    async def process() -> None:
        if event_type == FunctionRunCompletedEvent.get_event_type():
            parsed = FunctionRunCompletedEvent.model_validate(event)
            status = "COMPLETED"
            output = parsed.output_data
        else:
            parsed = FunctionRunFailedEvent.model_validate(event)
            status = "FAILED"
            output = {"error": parsed.error}
        fs_logger.info("Workflow: Received function run %s for %s", status, parsed.run_id)
        await job_queue.enqueue(
            "resume_workflow_run_for_function",
            function_run_id=str(parsed.run_id),
            run_status=status,
            output=output,
            _job_id=f"workflow-resume-function:{parsed.run_id}:{status}",
        )

    await inbox.process("workflow.function-resume", event, process)


@reliable_redis_stream_subscriber(
    router,
    AGENT_EVENTS_STREAM,
    group="workflow-agent-events",
    consumer="workflow-agent-events-consumer",
)
async def handle_agent_run_event(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Handle completed agent executions for workflow resumption."""

    if event.get("event_type") != AgentRunCompletedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = AgentRunCompletedEvent.model_validate(event)
        fs_logger.info(
            "Workflow: Received AgentRunCompleted for conversation %s",
            parsed.conversation_id,
        )
        async with uow_factory() as uow:
            waiting = await SqlAlchemyWorkflowRunWaitRepository(
                uow
            ).find_active_by_external_ref(
                WorkflowRunWaitType.AGENT, str(parsed.conversation_id)
            )
        if waiting is None:
            fs_logger.debug(
                "Workflow: Ignoring AgentRunCompleted for non-workflow conversation %s",
                parsed.conversation_id,
            )
            return

        await job_queue.enqueue(
            "resume_workflow_run_for_agent",
            agent_conversation_id=str(parsed.conversation_id),
            _job_id=f"workflow-resume-agent:{parsed.agent_run_id}",
        )

    await inbox.process("workflow.agent-resume", event, process)


@streaq_task(name="resume_workflow_run_for_function")
async def resume_workflow_run_for_function(
    function_run_id: str,
    run_status: str,
    output: dict | None = None,
):
    """Resume a workflow waiting for a function run."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    logger.info(
        f"Job: Resuming workflow run waiting for function run {function_run_id}"
    )

    async with worker_ctx.uow() as uow:
        service = RunResumeService(WorkflowEngine(uow))
        await service.resume_for_function_run(
            function_run_id=function_run_id,
            run_status=run_status,
            output=output,
        )


@streaq_task(name="resume_workflow_run_for_agent")
async def resume_workflow_run_for_agent(
    agent_conversation_id: str,
    attempt: int | None = None,
):
    """Resume a workflow waiting for an agent conversation execution."""
    worker_ctx: AppWorkerContext = streaq_worker.context

    _ = attempt
    logger.info(
        "Job: Resuming workflow run waiting for agent conversation",
        agent_conversation_id=agent_conversation_id,
    )

    async with worker_ctx.uow() as uow:
        service = RunResumeService(WorkflowEngine(uow))
        await service.resume_for_agent_conversation(
            conversation_id=agent_conversation_id,
        )


@streaq_cron("*/5 * * * *", name="reconcile_workflow_waits")
async def reconcile_workflow_waits():
    """Self-heal runs whose agent/function completion events were lost."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    async with worker_ctx.uow() as uow:
        service = RunResumeService(WorkflowEngine(uow))
        await service.reconcile_stale_waits()


# --- Schedule Integration ---


@reliable_redis_stream_subscriber(
    router,
    "schedule_events",
    group="workflow-schedule-events",
    consumer="workflow-schedule-events-consumer",
)
async def handle_schedule_events(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Handle schedule events to launch workflows."""
    event_type = event.get("event_type")

    if event_type != "schedule.fired":
        return

    async def process() -> None:
        await on_schedule_fired(event, fs_logger, job_queue)

    await inbox.process("workflow.schedule-start", event, process)


async def on_schedule_fired(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
):
    """Handle ScheduleFired: wake workflow waits or launch scheduled targets."""
    schedule_id = event.get("schedule_id")
    payload = event.get("payload")
    metadata = event.get("metadata")
    llm_output = event.get("llm_output")
    source_occurred_at = event.get("scheduled_at") or event.get("occurred_at")
    schedule_event_id = (
        event.get("source_event_id")
        or event.get("event_id")
        or event.get("id")
        or event.get("message_id")
        or event.get("occurred_at")
    )
    if not schedule_event_id:
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
        schedule_event_id = f"legacy:{hashlib.sha256(canonical.encode()).hexdigest()}"

    if not schedule_id:
        return

    fs_logger.info(f"Workflow: Received ScheduleFired for {schedule_id}")

    # Dedup redelivered schedule fires: streaq drops a duplicate enqueue while a
    # task with the same _job_id is still queued/running (its lock releases on
    # completion), which covers the common at-least-once redelivery window between
    # this handler receiving the event and ack-ing it. Durable idempotency across
    # the full window still rests on the run's unique constraint (workflow target)
    # and the Redis dedup key (agent target) inside handle_schedule_fired. Only set
    dedup_kwargs = {
        "_job_id": f"workflow-schedule-fire:{schedule_id}:{schedule_event_id}"
    }

    await job_queue.enqueue(
        "check_and_start_flows_for_schedule",
        schedule_id=str(schedule_id),
        payload=payload or {},
        metadata=metadata or {},
        llm_output=llm_output,
        schedule_event_id=str(schedule_event_id),
        source_occurred_at=(
            source_occurred_at.isoformat()
            if isinstance(source_occurred_at, datetime)
            else str(source_occurred_at)
            if source_occurred_at
            else None
        ),
        **dedup_kwargs,
    )


@streaq_task(name="check_and_start_flows_for_schedule")
async def check_and_start_flows_for_schedule(
    schedule_id: str,
    payload: dict,
    metadata: dict | None = None,
    llm_output: dict | None = None,
    schedule_event_id: str | None = None,
    source_occurred_at: str | None = None,
):
    """Check schedules and start or wake workflow runs."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    logger.info(f"Job: Checking flows for schedule {schedule_id}")

    async with worker_ctx.uow() as uow:
        service = ScheduleStartService(WorkflowEngine(uow))
        await service.handle_schedule_fired(
            schedule_id=schedule_id,
            payload=payload,
            metadata=metadata,
            llm_output=llm_output,
            schedule_event_id=schedule_event_id,
            source_occurred_at=(
                datetime.fromisoformat(source_occurred_at.replace("Z", "+00:00"))
                if source_occurred_at
                else None
            ),
        )
