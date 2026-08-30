"""Worker event handlers for agent runs."""

from __future__ import annotations

import asyncio
from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter
from sqlalchemy.exc import SQLAlchemyError
from streaq.task import TaskStatus

from app.composition.agent_usage import build_usage_service
from app.composition.authorization import create_authorization_service
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
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
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    AGENT_RUN_JOB_TIMEOUT_SECONDS,
    AppWorkerContext,
    streaq_cron,
    streaq_task,
    streaq_worker,
)
from app.modules.datastore.contracts import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreFileDeletedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.agent.services.run_resume import (
    agent_run_job_id,
    resume_parked_agent_runs,
)
from app.modules.agent.services.agent_memory_brief import invalidate_memory_brief
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.harnesses import (
    HarnessRegistry,
    PydanticAIHarness,
    RemoteHarness,
)
from app.modules.agent.infrastructure.harnesses.agent_host.artifacts import (
    PodFileAgentHostArtifactWriter,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.events.queued_followup import (
    start_followup_run_for_queued_messages,
)
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.conversation_service import ConversationService
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


_FILE_WRITE_EVENT_TYPES = frozenset(
    {
        DatastoreFileCreatedEvent.get_event_type(),
        DatastoreFileUpdatedEvent.get_event_type(),
        DatastoreFileDeletedEvent.get_event_type(),
    }
)


@reliable_redis_stream_subscriber(
    router,
    DATASTORE_EVENTS_STREAM,
    group="agent-memory-brief-invalidation",
    consumer="agent-memory-brief-invalidation-consumer",
)
async def on_datastore_file_written(event: dict, fs_logger: Logger):
    """Drop a cached memory section when the file behind it changes.

    The pod tools already invalidate inline, which covers agents writing through
    `pod_write_file`. This covers the other writer, and it is the common one:
    an agent with a shell writes memory with `lemma files write`, which reaches
    the datastore over HTTP in the API process and never enters the worker that
    ran the agent. Without this, the shell path is stale until the TTL.

    No inbox and no idempotency key -- deleting a cache entry twice costs
    nothing, and a delivery this misses costs only the TTL, which is the
    behaviour that existed before any invalidation at all.
    """
    if event.get("event_type") not in _FILE_WRITE_EVENT_TYPES:
        return
    pod_id = event.get("pod_id")
    path = event.get("path")
    if not pod_id or not path:
        return
    actor_id = event.get("actor_id")
    await invalidate_memory_brief(
        pod_id=UUID(str(pod_id)),
        path=str(path),
        user_id=UUID(str(actor_id)) if actor_id else None,
    )


def conversation_title_job_id(conversation_id: UUID) -> str:
    return f"conv-title:{conversation_id}"


def provide_job_queue() -> SharedStreaqJobQueue:
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def build_harness_registry() -> HarnessRegistry:
    uow_factory = provide_uow_factory()
    return HarnessRegistry(
        [
            PydanticAIHarness(),
            # One harness for every Agent Host tool; which one runs is decided
            # by the profile's harness_id, not by the registry.
            RemoteHarness(
                uow_factory,
                # Without a writer the harness drops every image an agent
                # produces: a content block with no text renders to nothing,
                # so Codex's `$imagegen` output reached this process and was
                # discarded. The host already publishes those blocks and the
                # writer already knows how to store them.
                artifact_writer=PodFileAgentHostArtifactWriter(uow_factory),
            ),
        ]
    )


@reliable_redis_stream_subscriber(
    router,
    AGENT_EVENTS_STREAM,
    group="agent-events",
    consumer="agent-events-consumer",
)
async def handle_agent_control_event(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    event_model = CONTROL_EVENT_MODELS.get(event.get("event_type"))
    if event_model is None:
        return

    async def process() -> None:
        parsed = event_model.model_validate(event)
        await _process_agent_control_event(
            parsed,
            fs_logger=fs_logger,
            job_queue=job_queue,
            uow_factory=uow_factory,
        )

    await inbox.process("agent.control", event, process)


async def _process_agent_control_event(
    parsed: AgentRunStartedEvent | AgentRunStopRequestedEvent | AgentRunCompletedEvent,
    *,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
    uow_factory: UnitOfWorkFactory,
) -> None:
    if isinstance(parsed, AgentRunStartedEvent):
        await enqueue_agent_run(parsed, fs_logger=fs_logger, job_queue=job_queue)
        # The title only needs the user's first message -- already saved by
        # the time this event fires -- not the agent's reply, so it does not
        # need to wait for the run to finish. Starting it here rather than on
        # completion means a slow or long-running turn no longer leaves the
        # conversation title-less for its whole duration. The deterministic
        # job id dedups across turns, so this runs at most once per
        # conversation; the job itself no-ops if a title already exists.
        await job_queue.enqueue(
            "generate_conversation_title",
            context={"conversation_id": str(parsed.conversation_id)},
            _job_id=conversation_title_job_id(parsed.conversation_id),
        )
        return
    if isinstance(parsed, AgentRunCompletedEvent):
        # Belt and suspenders: the same deterministic job id means this is a
        # no-op on the (now-common) path where the run-started enqueue above
        # already generated the title, and a fallback for anything that
        # reaches completion without having gone through that event.
        await job_queue.enqueue(
            "generate_conversation_title",
            context={"conversation_id": str(parsed.conversation_id)},
            _job_id=conversation_title_job_id(parsed.conversation_id),
        )
        # Anything the person sent while that run was busy has been sitting
        # unanswered: the run it joined had already read its history.
        await start_followup_run_for_queued_messages(parsed, uow_factory=uow_factory)
        return
    if isinstance(parsed, AgentRunStopRequestedEvent):
        job_id = agent_run_job_id(parsed.agent_run_id)
        task_status = await job_queue.status(job_id)
        if task_status == TaskStatus.RUNNING:
            return

        # Do not call streaq abort here. A queued task can race into RUNNING
        # between status() and abort(), and aborting that internal cancel scope
        # can poison the worker task. Mark the run terminal instead; if the
        # streaq task later starts, process_agent_run exits as a no-op.
        event_data = {
            "aborted": False,
            "task_status": task_status.value,
        }
        async with uow_factory() as uow:
            repo = ConversationRepository(uow)
            finish_result = await repo.finish_agent_run(
                agent_run_id=parsed.agent_run_id,
                status=AgentRunStatus.STOPPED,
            )
            if finish_result is not None and finish_result.updated:
                repo.collect_events(
                    [
                        AgentRunCompletedEvent(
                            conversation_id=parsed.conversation_id,
                            agent_run_id=parsed.agent_run_id,
                            status=finish_result.status,
                            data=event_data,
                        )
                    ]
                )
        if finish_result is None or not finish_result.updated:
            return

        await publish_conversation_event(
            parsed.conversation_id,
            completed_payload(
                conversation_id=parsed.conversation_id,
                agent_run_id=parsed.agent_run_id,
                status=finish_result.status.value,
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
        return False
    return True


@streaq_task(name="process_agent_run", timeout=AGENT_RUN_JOB_TIMEOUT_SECONDS)
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
    from app.composition.agent_surface_runtime import build_progress_observer

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
            observer=build_progress_observer(
                uow_factory=worker_ctx.uow_factory,
                service_factory=worker_ctx.build_surface_event_handler,
            ),
        )
    except asyncio.CancelledError:
        logger.debug(
            "agent.handlers.process_agent_run_cancelled_run.diagnostic",
            agent_run_id=agent_run_id,
        )


@streaq_task(name="reconcile_agent_approval")
async def reconcile_agent_approval(
    context: dict[str, str | None],
) -> None:
    """Execute a durable approval decision outside the HTTP request deadline."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    await reconcile_agent_approval_now(context, uow_factory=worker_ctx.uow_factory)


async def reconcile_agent_approval_now(
    context: dict[str, str | None],
    *,
    uow_factory: UnitOfWorkFactory,
) -> None:
    """Reconcile one already-recorded decision; split out for focused tests.

    Both lookups can legitimately come back empty — the conversation was deleted,
    or this job outran the transaction that recorded the decision — and neither
    is an error worth retrying, so both simply return.
    """
    conversation_id = UUID(str(context["conversation_id"]))
    approval_id = str(context["approval_id"])
    pod_id = UUID(str(context["pod_id"]))

    async with uow_factory() as uow:
        conversation_repository = ConversationRepository(uow)
        conversation = await conversation_repository.get_conversation(conversation_id)
        if conversation is None:
            return
        recorded = await conversation_repository.get_approval_decision(
            conversation_id=conversation_id,
            approval_id=approval_id,
        )
        if recorded is None:
            return
        decision, response = recorded
        service = ConversationService(
            uow=uow,
            conversation_repository=conversation_repository,
            agent_repository=AgentRepository(uow),
            authorization_service=create_authorization_service(uow),
            usage_service=build_usage_service(uow),
        )
        # An approved request_approval runs its wrapped tool with a user's
        # authority, which needs that user's authorization context bound — the
        # request that recorded this decision had one, this worker job does not.
        # Both halves must name the SAME user or the tool runs with one
        # principal's ambient permissions under another's identity. It is the
        # conversation owner, per resolve_user_approval_internal's contract and
        # matching the surface approval path: the agent acts for the owner, and
        # whoever clicked approve is deciding, not lending their authority.
        # ``user_id`` from the job context is the resolver and is only recorded
        # as such, which already happened before this job was queued.
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )
        async with context_scope(auth_ctx):
            await service.resolve_user_approval_internal(
                conversation=conversation,
                approval_id=approval_id,
                user_id=conversation.user_id,
                pod_id=pod_id,
                decision=decision,
                response=response,
            )


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


# Sweep stale runs only well after the agent-run task timeout, so a legitimately
# long-running agent (up to AGENT_RUN_JOB_TIMEOUT_SECONDS) is never swept; by
# then the task is definitively gone (crash/OOM/forced shutdown losing the
# finalization race) and the run must be failed so it doesn't sit in RUNNING
# forever.
_ORPHANED_RUN_CUTOFF_SECONDS = AGENT_RUN_JOB_TIMEOUT_SECONDS + 300


@streaq_cron("*/2 * * * *", name="resume_interrupted_agent_runs")
async def resume_interrupted_agent_runs() -> None:
    """Hand runs parked by a departing worker to a live one."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    await resume_parked_agent_runs(
        uow_factory=worker_ctx.uow_factory,
        job_queue=worker_ctx.job_queue,
    )


@streaq_cron("5-59/10 * * * *", name="reconcile_orphaned_agent_runs")
async def reconcile_orphaned_agent_runs() -> None:
    """Self-heal agent runs stuck RUNNING after a hard crash.

    Narrower than it used to be. A worker shut down with SIGTERM now parks its
    runs INTERRUPTED and `resume_interrupted_agent_runs` hands them on, so this
    is left with what a worker never got the chance to park: SIGKILL, OOM, and
    the residual race where finalization lost to engine disposal.

    Those runs cannot be resumed safely -- nothing closed their outstanding tool
    calls, and staleness alone cannot tell a dead worker from a live peer without
    a heartbeat. So they still fail terminally, which publishes the same
    lifecycle + SSE events a normal finish does: the UI updates and any waiting
    workflow is unblocked.
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
                    event_data = {
                        "error": "Agent run was interrupted (worker restart or crash)"
                    }
                    repo.collect_events(
                        [
                            AgentRunCompletedEvent(
                                conversation_id=run.conversation_id,
                                agent_run_id=run.id,
                                status=finish_result.status,
                                data=event_data,
                            )
                        ]
                    )
                    finalized.append(
                        (run.conversation_id, run.id, finish_result.status)
                    )
    except Exception:
        logger.error(
            "agent.handlers.reconcile_orphaned_agent_runs_cron.failed", exc_info=True
        )
        return

    if not finalized:
        return

    logger.debug(
        "agent.handlers.reconciled_d_orphaned_agent_run.diagnostic",
        count=len(finalized),
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
        except Exception:
            logger.error(
                "agent.handlers.publishing_reconciled_run_realtime_update.failed",
                agent_run_id=agent_run_id,
                exc_info=True,
            )


@streaq_cron("1-59/5 * * * *", name="reconcile_agent_host_dispatch")
async def reconcile_agent_host_dispatch() -> None:
    """Reconcile Agent Host leases against the runs they belong to.

    Two things nobody else does once the worker driving a run is gone. Cancel
    host runs whose Lemma run already ended, so a machine on someone's desk
    stops executing tools for a turn we have reported as failed. And advance
    leases whose heartbeat lapsed, which otherwise stay non-terminal forever and
    are never collected by retention.
    """
    from app.modules.agent.infrastructure.agent_host import recovery
    from app.modules.agent.infrastructure.agent_host.channels import poke_host

    worker_ctx: AppWorkerContext = streaq_worker.context
    # Only database trouble is swallowed here: it is the transient failure this
    # sweep expects, and the next tick is five minutes away. Anything else is a
    # bug and surfaces through the worker's own job-failure path.
    # Two transactions, not one, and that is a deadlock fix rather than a
    # style choice. Both halves touch leases and commands; run together they
    # hold lease locks from `cancel_abandoned_host_runs` while
    # `reconcile_expired_leases` goes on to take command locks. The host poll
    # walks the same two tables the other way round -- commands first, then a
    # blocking lease lock -- so the two can wedge (ABBA), Postgres aborts one
    # with 40P01, and the poll surfaces it as a 500 because it catches only
    # `AgentHostRepositoryError`.
    #
    # Committing between them means no lease lock is ever held across a command
    # acquisition, which removes this side of the cycle. The two sweeps are
    # independent -- one cancels runs Lemma already finished, the other advances
    # lapsed leases -- so splitting them costs nothing but a second round trip
    # every five minutes.
    try:
        async with worker_ctx.uow() as uow:
            host_ids = await recovery.cancel_abandoned_host_runs(uow.session)
            await uow.commit()
        async with worker_ctx.uow() as uow:
            await recovery.reconcile_expired_leases(uow.session)
            await uow.commit()
    except SQLAlchemyError:
        logger.error(
            "agent.handlers.reconcile_agent_host_dispatch_cron.failed", exc_info=True
        )
        return
    # Poke outside the transaction: the host is long-polling, and without this
    # the cancel waits out its poll deadline. poke_host never raises.
    for host_id in dict.fromkeys(host_ids):
        await poke_host(host_id)


@streaq_cron("23 4 * * *", name="cleanup_agent_host_retained_state")
async def cleanup_agent_host_retained_state() -> None:
    """Collect spent Agent Host pairings, commands, and leases.

    Without this registration the sweep existed but never ran, so dispatch rows
    accumulated for the lifetime of the deployment and consumed pairing codes
    were never purged.
    """
    from app.modules.agent.infrastructure.agent_host import recovery

    worker_ctx: AppWorkerContext = streaq_worker.context
    try:
        async with worker_ctx.uow() as uow:
            await recovery.cleanup_retained_state(uow.session)
            await uow.commit()
    except SQLAlchemyError:
        logger.error(
            "agent.handlers.cleanup_agent_host_retained_state_cron.failed",
            exc_info=True,
        )
