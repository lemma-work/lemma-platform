"""Background job handlers and FastStream event consumers for Workflow module."""

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
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.domain.wait import WorkflowRunWaitType
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
        async with uow_factory() as uow:
            waiting = await SqlAlchemyWorkflowRunWaitRepository(
                uow
            ).find_active_by_external_ref(
                WorkflowRunWaitType.AGENT, str(parsed.conversation_id)
            )
        if waiting is None:
            logger.debug(
                "workflow.handlers.ignoring_agentruncompleted_non_workflow_conversation.observed",
                conversation_id=parsed.conversation_id,
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
    logger.debug(
        "workflow.handlers.job_resuming_workflow_run_waiting.observed",
        function_run_id=function_run_id,
    )

    async with worker_ctx.uow() as uow:
        service = RunResumeService(build_workflow_engine(uow))
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
    logger.debug(
        "workflow.handlers.job_resuming_workflow_run_waiting.observed",
        agent_conversation_id=agent_conversation_id,
    )

    async with worker_ctx.uow() as uow:
        service = RunResumeService(build_workflow_engine(uow))
        await service.resume_for_agent_conversation(
            conversation_id=agent_conversation_id,
        )


@streaq_cron("1-59/5 * * * *", name="reconcile_workflow_waits")
async def reconcile_workflow_waits():
    """Self-heal runs whose agent/function completion events were lost."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    async with worker_ctx.uow() as uow:
        service = RunResumeService(build_workflow_engine(uow))
        await service.reconcile_stale_waits()


@streaq_cron("41 * * * *", name="prune_workflow_run_waits")
async def prune_workflow_run_waits() -> None:
    """Reclaim finished machine waits. Human approvals are never touched.

    A wait row is scaffolding for one step: the engine records what a run is
    blocked on, and once the function returns or the agent finishes, the row has
    served its purpose. Nothing removed them, so they accumulated -- in
    production, roughly 105,000 machine waits against 5,700 human ones.

    ``HUMAN`` waits are excluded by predicate at any age and any status. Those
    are the record of who was asked to approve something and what they said,
    which is not scaffolding and not ours to age out. If that distinction ever
    stops holding, this sweep is the thing that must change, not the retention
    window.

    Offset off the hour to stay clear of the other delete-heavy sweeps.
    """
    from app.core.config import settings
    from app.modules.workflow.infrastructure.repositories.wait_retention import (
        prune_terminal_machine_waits,
    )

    deleted = await prune_terminal_machine_waits(
        async_session_maker,
        retention_days=settings.workflow_wait_retention_days,
        batch_size=settings.workflow_wait_retention_batch_size,
        budget_seconds=settings.workflow_wait_retention_budget_seconds,
    )
    if deleted:
        logger.debug(
            "workflow.handlers.prune_workflow_run_waits.observed",
            deleted_count=deleted,
        )


@streaq_cron("2-59/5 * * * *", name="reconcile_agent_snoozes")
async def reconcile_agent_snoozes():
    """Wake snoozed conversations whose scheduler event was lost.

    Unlike an agent or function wait there is no external system to poll — a
    timer only has to elapse — so a wait overdue by more than the sweep's grace
    period is simply fired here. Waking is idempotent (the wake claims the row
    under a lock), which makes a duplicate with the primary timer harmless.
    """
    from app.modules.agent.services.snooze_reconcile_service import (
        SnoozeReconcileService,
    )

    # Opens a session per step itself: one wait's failed wake must not roll back
    # the transaction the rest of the batch is running in.
    await SnoozeReconcileService().reconcile_due_waits()


@streaq_cron("3-59/5 * * * *", name="expire_past_due_notifications")
async def expire_past_due_notifications():
    """Close out notifications nobody answered before their deadline.

    Not a failure — people are busy, and the deadline is 72h precisely so that
    ordinary out-of-hours delay does not trip it. But a row that stays OPEN
    forever is an inbox badge that never clears and an asking run waiting on
    something that will never arrive.

    Lives beside the two wait sweeps deliberately: same cadence, same batch
    discipline, same "the timer may have been lost, the row is the truth" shape.
    A third invention here would drift from the other two.
    """
    from app.composition.workflow_notifications import expire_past_due_notifications

    worker_ctx: AppWorkerContext = streaq_worker.context
    async with worker_ctx.uow() as uow:
        expired = await expire_past_due_notifications(uow)
        if expired:
            logger.info(
                "agent_surfaces.notifications.expired.observed",
                count=expired,
            )
        await uow.commit()


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

    # Validate inside the inbox, not before it. The inbox turns a ValidationError
    # into a TERMINAL outcome and acks; raising out here instead would nack an
    # unparseable event forever, since the 60s reclaim subscriber has no attempt
    # cap and would redeliver the same poison message indefinitely.
    async def process() -> None:
        fired = ScheduleFired.model_validate(event)
        await on_schedule_fired(fired, fs_logger, job_queue)

    await inbox.process("workflow.schedule-start", event, process)


async def on_schedule_fired(
    event: ScheduleFired,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
):
    """Handle ScheduleFired: wake workflow waits or launch scheduled targets."""
    schedule_id = event.schedule_id
    source_occurred_at = event.scheduled_at or event.occurred_at
    schedule_event_id = event.source_event_id

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
        # Stays None across the queue boundary: str(None) would arrive as the
        # literal "None" and blow up on UUID() instead of being recognised as
        # an owner-less legacy timer.
        user_id=str(event.user_id) if event.user_id else None,
        payload=event.payload,
        metadata=event.metadata or {},
        llm_output=event.llm_output,
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


@streaq_task(name="check_and_start_flows_for_schedule", max_tries=10)
async def check_and_start_flows_for_schedule(
    schedule_id: str,
    user_id: str | None,
    payload: dict,
    schedule_event_id: str,
    metadata: dict | None = None,
    llm_output: dict | None = None,
    source_occurred_at: str | None = None,
):
    """Check schedules and start or wake workflow runs."""
    worker_ctx: AppWorkerContext = streaq_worker.context

    async with worker_ctx.uow() as uow:
        service = ScheduleStartService(build_workflow_engine(uow))
        await service.handle_schedule_fired(
            schedule_id=schedule_id,
            user_id=user_id,
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


@streaq_cron("4-59/5 * * * *", name="recover_schedule_runs")
async def recover_schedule_runs() -> None:
    from app.modules.schedule.contracts.run_recovery import recover_schedule_runs

    worker_ctx: AppWorkerContext = streaq_worker.context
    async with worker_ctx.uow() as uow:
        result = await recover_schedule_runs(uow)
    # Still a warning, and deliberately so. This used to fire twelve times an
    # hour and was read as routine maintenance, but that was the counting: rows
    # the sweep inspected and correctly left alone were reported as
    # `reconciled`, so a sweep doing nothing at all looked busy. Now the three
    # counters below only move when the event-driven outcome path missed
    # something, a dispatch was lost, or a run was abandoned -- each of which is
    # the safety net catching a failure somewhere else, and worth a warning.
    if result.redelivered or result.reconciled or result.dead_lettered:
        logger.warning(
            "schedule.runs.recovered",
            redelivered=result.redelivered,
            reconciled=result.reconciled,
            dead_lettered=result.dead_lettered,
            still_running=result.still_running,
        )
