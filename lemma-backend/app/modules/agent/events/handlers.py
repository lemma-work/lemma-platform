"""Worker event handlers for agent runs."""

from __future__ import annotations

import asyncio
from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter
from streaq.task import TaskStatus

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.infrastructure.events.stream_subscriber import redis_stream_sub
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    JOB_TIMEOUT_SECONDS,
    AppWorkerContext,
    streaq_cron,
    streaq_task,
    streaq_worker,
)
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.infrastructure.harnesses import (
    DaemonHarness,
    HarnessRegistry,
    PydanticAIHarness,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.realtime import (
    completed_payload,
    publish_conversation_event,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()

CONTROL_EVENT_MODELS = {
    AgentRunStartedEvent.get_event_type(): AgentRunStartedEvent,
    AgentRunStopRequestedEvent.get_event_type(): AgentRunStopRequestedEvent,
    AgentRunCompletedEvent.get_event_type(): AgentRunCompletedEvent,
}


def conversation_title_job_id(conversation_id: UUID) -> str:
    return f"conv-title:{conversation_id}"

def provide_job_queue() -> SharedStreaqJobQueue:
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def build_harness_registry() -> HarnessRegistry:
    reconnect_grace_seconds = settings.daemon_reconnect_grace_seconds
    return HarnessRegistry(
        [
            PydanticAIHarness(),
            DaemonHarness(HarnessKind.CODEX, reconnect_grace_seconds=reconnect_grace_seconds),
            DaemonHarness(HarnessKind.CLAUDE_CODE, reconnect_grace_seconds=reconnect_grace_seconds),
            DaemonHarness(HarnessKind.OPENCODE, reconnect_grace_seconds=reconnect_grace_seconds),
            DaemonHarness(HarnessKind.CURSOR, reconnect_grace_seconds=reconnect_grace_seconds),
            DaemonHarness(HarnessKind.ANTIGRAVITY, reconnect_grace_seconds=reconnect_grace_seconds),
        ]
    )


def agent_run_job_id(agent_run_id: UUID) -> str:
    return f"agent-run:{agent_run_id}"


@router.subscriber(
    stream=redis_stream_sub(
        AGENT_EVENTS_STREAM,
        group="agent-events",
        consumer="agent-events-consumer",
    )
)
async def handle_agent_control_event(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
):
    event_model = CONTROL_EVENT_MODELS.get(event.get("event_type"))
    if event_model is None:
        return

    parsed = event_model.model_validate(event)
    if isinstance(parsed, AgentRunStartedEvent):
        await enqueue_agent_run(parsed, fs_logger=fs_logger, job_queue=job_queue)
        return
    if isinstance(parsed, AgentRunCompletedEvent):
        # Generate a title once the first run finishes. The deterministic job id
        # dedups across turns, so this runs at most once per conversation; the
        # job itself no-ops if a title already exists.
        await job_queue.enqueue(
            "generate_conversation_title",
            context={"conversation_id": str(parsed.conversation_id)},
            _job_id=conversation_title_job_id(parsed.conversation_id),
        )
        return
    if isinstance(parsed, AgentRunStopRequestedEvent):
        job_id = agent_run_job_id(parsed.agent_run_id)
        task_status = await job_queue.status(job_id)
        if task_status == TaskStatus.RUNNING:
            fs_logger.info(
                "Agent run stop requested; runner will stop cooperatively: %s",
                parsed.agent_run_id,
            )
            return

        # Do not call streaq abort here. A queued task can race into RUNNING
        # between status() and abort(), and aborting that internal cancel scope
        # can poison the worker task. Mark the run terminal instead; if the
        # streaq task later starts, process_agent_run exits as a no-op.
        async with uow_factory() as uow:
            finish_result = await ConversationRepository(uow).finish_agent_run(
                agent_run_id=parsed.agent_run_id,
                status=AgentRunStatus.STOPPED,
            )
        if finish_result is None or not finish_result.updated:
            return

        fs_logger.info(
            "Agent run stopped before worker execution: %s",
            parsed.agent_run_id,
        )
        event_data = {
            "aborted": False,
            "task_status": task_status.value,
        }
        await publish_conversation_event(
            parsed.conversation_id,
            completed_payload(
                conversation_id=parsed.conversation_id,
                agent_run_id=parsed.agent_run_id,
                status=finish_result.status.value,
                data=event_data,
            ),
        )
        await EventPublisher.publish(
            AGENT_EVENTS_STREAM,
            AgentRunCompletedEvent(
                conversation_id=parsed.conversation_id,
                agent_run_id=parsed.agent_run_id,
                status=finish_result.status,
                data=event_data,
            ),
        )


async def enqueue_agent_run(
    event: AgentRunStartedEvent,
    *,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
) -> bool:
    job = await job_queue.enqueue(
        "process_agent_run",
        context={
            "agent_run_id": str(event.agent_run_id),
            "conversation_id": str(event.conversation_id),
            "user_id": str(event.user_id),
            "pod_id": str(event.pod_id),
            "agent_name": event.agent_name,
        },
        _job_id=agent_run_job_id(event.agent_run_id),
    )
    if job is None:
        fs_logger.info("Skipped duplicate agent run enqueue: %s", event.agent_run_id)
        return False
    fs_logger.info("Enqueued agent run: %s", event.agent_run_id)
    return True


@streaq_task(name="process_agent_run")
async def process_agent_run(
    context: dict[str, str | None],
):
    worker_ctx: AppWorkerContext = streaq_worker.context
    agent_run_id = UUID(str(context["agent_run_id"]))
    user_id = UUID(str(context["user_id"]))
    pod_id = UUID(str(context["pod_id"]))
    agent_name = context.get("agent_name")

    runner = AgentRunnerService(
        uow_factory=worker_ctx.uow_factory,
        harness_registry=build_harness_registry(),
    )
    from app.modules.agent_surfaces.services.progress_observer import (
        SurfaceAgentRunProgressObserver,
    )

    # Safety net: if a cancellation arrives before/during runner.execute (e.g.
    # streaq task timeout, worker shutdown) and propagates as CancelledError
    # past execute's own handler, swallow it here. Re-raising CancelledError
    # into streaq's `with scope:` block triggers
    # "Attempted to exit a cancel scope that isn't the current task's current
    # cancel scope" — a RuntimeError that crashes the entire worker. The run
    # is already finalized inside execute; there is nothing useful to do here.
    try:
        await runner.execute(
            agent_run_id=agent_run_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            observer=SurfaceAgentRunProgressObserver(
                uow_factory=worker_ctx.uow_factory,
                service_factory=worker_ctx.build_surface_event_handler,
            ),
        )
    except asyncio.CancelledError:
        logger.warning("process_agent_run cancelled run=%s", agent_run_id)


@streaq_task(name="generate_conversation_title")
async def process_conversation_title(
    context: dict[str, str | None],
):
    from app.modules.agent.services.conversation_title_service import (
        ConversationTitleService,
    )

    worker_ctx: AppWorkerContext = streaq_worker.context
    conversation_id = UUID(str(context["conversation_id"]))
    await ConversationTitleService(
        uow_factory=worker_ctx.uow_factory
    ).generate_title_if_absent(conversation_id)


# Sweep stale runs only well after the streaq task timeout, so a legitimately
# long-running agent (up to JOB_TIMEOUT_SECONDS) is never swept; by then the
# task is definitively gone (crash/OOM/forced shutdown losing the finalization
# race) and the run must be failed so it doesn't sit in RUNNING forever.
_ORPHANED_RUN_CUTOFF_SECONDS = JOB_TIMEOUT_SECONDS + 300


@streaq_cron("*/10 * * * *", name="reconcile_orphaned_agent_runs")
async def reconcile_orphaned_agent_runs() -> None:
    """Self-heal agent runs stuck non-terminal after a worker crash/restart.

    The grace_period on the worker lets a SIGTERM-interrupted run finalize
    itself; this cron is the backstop for hard crashes (SIGKILL/OOM) and any
    residual race where finalization lost to engine disposal. Marking the run
    FAILED here publishes the same lifecycle + SSE events a normal finish does,
    so the UI updates and any waiting workflow is unblocked.
    """
    worker_ctx: AppWorkerContext = streaq_worker.context
    try:
        async with worker_ctx.uow() as uow:
            repo = ConversationRepository(uow)
            stale = await repo.list_stale_active_runs(
                cutoff_seconds=_ORPHANED_RUN_CUTOFF_SECONDS,
            )
            finalized: list[tuple[UUID, UUID, AgentRunStatus]] = []
            for run in stale:
                finish_result = await repo.finish_agent_run(
                    agent_run_id=run.id,
                    status=AgentRunStatus.FAILED,
                    error="Agent run was interrupted (worker restart or crash)",
                )
                if finish_result is not None and finish_result.updated:
                    finalized.append(
                        (run.conversation_id, run.id, finish_result.status)
                    )
    except Exception as exc:
        logger.error("reconcile_orphaned_agent_runs cron failed: %s", exc)
        return

    if not finalized:
        return

    logger.warning(
        "Reconciled %d orphaned agent run(s) to FAILED", len(finalized)
    )
    # Publish outside the UoW (mirrors handle_agent_control_event's stop path)
    # so SSE clients refresh and workflow waits resume promptly.
    for conversation_id, agent_run_id, status in finalized:
        event_data = {"error": "Agent run was interrupted (worker restart or crash)"}
        try:
            await publish_conversation_event(
                conversation_id,
                completed_payload(
                    conversation_id=conversation_id,
                    agent_run_id=agent_run_id,
                    status=status.value,
                    data=event_data,
                ),
            )
            await EventPublisher.publish(
                AGENT_EVENTS_STREAM,
                AgentRunCompletedEvent(
                    conversation_id=conversation_id,
                    agent_run_id=agent_run_id,
                    status=status,
                    data=event_data,
                ),
            )
        except Exception as exc:
            logger.error(
                "Failed publishing reconciled-run events run=%s: %s",
                agent_run_id,
                exc,
            )
