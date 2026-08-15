"""Shared streaq worker runtime and dependency context."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from faststream.redis import RedisBroker
from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind
from streaq import Worker

from app.core.config import settings
from app.core.infrastructure.channels.channel_service import channel_service
from app.core.infrastructure.cache.redis_json_cache import close_redis_json_caches
from app.core.infrastructure.redis.client import close_redis_clients, get_redis
from app.core.infrastructure.db.session import (
    async_session_maker,
    get_engine,
    close_engine,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.events.message_bus import (
    close_message_bus,
    get_message_bus,
)
from app.core.infrastructure.events.outbox import outbox_dispatcher_lifespan
from app.core.infrastructure.events.stream_observability import (
    redis_stream_snapshot_loop,
)
from app.core.observability.backlog_gauges import backlog_gauge_loop
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    close_streaq_job_queue,
    get_streaq_job_queue,
    job_context_key,
)
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.core.log.log import (
    get_dependency_logger,
    get_logger,
    setup_logging,
    validate_release_identity,
)
from app.core.observability.telemetry import (
    init_telemetry,
    instrument_database_engine,
    shutdown_telemetry,
)
from app.core.request_context import bind_job_context, create_background_task

if TYPE_CHECKING:
    from app.core.registry.contract import LemmaModule

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
job_counter = meter.create_counter("lemma.worker.jobs")
job_duration = meter.create_histogram("lemma.worker.job.duration", unit="ms")

class Lane(StrEnum):
    """Which queue a task runs on.

    Before lanes, every task type — agent runs, surface messages, workflow
    resumes, pod imports, document ingestion — shared one queue and one
    concurrency budget. A bulk upload could therefore occupy every worker slot
    and stall interactive work behind it. Splitting the queue is what makes the
    two classes of work independent; they are separate Redis queues, so a deep
    bulk backlog is invisible to the interactive lane.
    """

    #: Latency-sensitive, user-facing work. Someone is waiting on it.
    INTERACTIVE = "interactive"
    #: Throughput-oriented background work. Slower is acceptable; starving the
    #: interactive lane is not.
    BULK = "bulk"


#: The lane that owns process-wide startup (see ``secondary_lane_lifespan``).
_PRIMARY_LANE = Lane.INTERACTIVE
_SECONDARY_LANE_STARTUP_TIMEOUT_SECONDS = 120.0

#: task name -> lane, populated by the @streaq_task/@streaq_cron decorators.
#: The enqueue side reads this to route a job to the correct queue, so callers
#: never name a queue and a task can be re-laned in exactly one place.
TASK_LANES: dict[str, Lane] = {}

_primary_lane_context: AppWorkerContext | None = None
_primary_lane_ready = asyncio.Event()

#: Tasks running the non-primary lanes, so the primary's teardown can stop them
#: before it disposes the infrastructure they share.
_secondary_lane_tasks: list[asyncio.Task[None]] = []


def _silence_lane_signal_handler(worker: Worker[AppWorkerContext]) -> None:
    """Stop a non-primary lane from competing for the process's signals.

    streaq starts `signal_handler` for every worker regardless of its
    `handle_signals` argument, and each one opens an anyio signal receiver.
    asyncio's `add_signal_handler` keeps only the last registration per signal,
    so with more than one lane the SIGTERM goes to whichever registered last —
    and if that is not the primary, the lane that receives it cancels only its
    own scope while the primary keeps running.

    Replacing the coroutine on the instance is the smallest thing that works:
    the task still exists and still ends with the worker's task group, it just
    never claims the signal.
    """

    async def _never_receives_signals(_scope: object) -> None:
        await asyncio.Event().wait()  # until the lane's task group unwinds

    worker.signal_handler = _never_receives_signals  # type: ignore[method-assign]


def _install_task_dump_handler() -> None:
    """Print every pending coroutine's stack on SIGQUIT.

    A worker that stops responding to SIGTERM shows nothing useful in a thread
    dump: `faulthandler` reports the event loop sitting in `select()`, which is
    what an idle loop always looks like. The question is always *which awaited
    coroutine is not finishing*, and only the task list answers it. SIGQUIT is
    free — neither streaq nor anything else here uses it.
    """
    import signal

    def _dump(*_args: object) -> None:
        for task in asyncio.all_tasks():
            frames = "".join(
                traceback.format_stack(task.get_coro().cr_frame)  # type: ignore[union-attr]
                if getattr(task.get_coro(), "cr_frame", None)
                else []
            )
            logger.warning(
                "infrastructure.streaq_runtime.pending_task_dump.diagnostic",
                task_name=task.get_name(),
                frames=frames[-2000:],
            )

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGQUIT, _dump)
    except (NotImplementedError, RuntimeError):  # pragma: no cover - platform
        pass


async def _stop_secondary_lanes() -> None:
    """Cancel the non-primary lanes and wait, briefly, for them to unwind."""
    tasks = [task for task in _secondary_lane_tasks if not task.done()]
    _secondary_lane_tasks.clear()
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    _, pending = await asyncio.wait(tasks, timeout=_SECONDARY_LANE_SHUTDOWN_SECONDS)
    if pending:
        # Named, because "the worker had to be killed" is not a diagnosis.
        logger.warning(
            "infrastructure.streaq_runtime.lane_shutdown_timed_out.degraded",
            lanes=",".join(sorted(task.get_name() for task in pending)),
            timeout_seconds=_SECONDARY_LANE_SHUTDOWN_SECONDS,
        )


def lane_queue_name(lane: Lane) -> str:
    """Redis queue name for a lane.

    The interactive lane keeps the bare configured name so existing queues,
    dashboards and any in-flight jobs survive the upgrade untouched; only the
    new bulk lane gets a suffix.
    """
    base = settings.worker_queue_name
    return base if lane is Lane.INTERACTIVE else f"{base}-{lane.value}"


def lane_concurrency(lane: Lane) -> int:
    if lane is Lane.BULK:
        return settings.worker_bulk_concurrency
    return settings.worker_concurrency


def ensure_task_lanes_registered(modules: Sequence[LemmaModule] | None = None) -> None:
    """Populate ``TASK_LANES`` in a process that only *publishes* jobs.

    The decorators fill ``TASK_LANES`` as a side effect of importing each
    module's handlers, which the worker entrypoint does via ``app.events``. The
    API imports controllers, not handlers — so its ``TASK_LANES`` was empty and
    every bulk task it enqueued was routed to the interactive queue, where the
    interactive worker read a task it had never registered and dropped it with
    "missing function". Pod bundle export, import, and GitHub publish are
    enqueued only from the API, so all three were silently doing nothing.

    Registration is import-for-side-effect and touches no I/O, so the publisher
    can do it on demand. Skipped when the table is already populated: the worker
    registers at import scope and must not register a second time.

    ``modules`` is the composed module list — lemma-cloud installs more than
    OSS, and a cloud-only task missing from this table lands back on the exact
    bug above. Callers with no module list (the lazy fallback on the enqueue
    path) get the OSS set.
    """
    if TASK_LANES:
        return
    # Deferred: the registry imports the modules that import this one.
    from app.core.registry.assembly import import_module_tasks
    from app.core.registry.installed import OSS_MODULES

    # The core's own crons, which app/events.py imports explicitly.
    from app.core.infrastructure.events import tasks as _core_tasks  # noqa: F401

    import_module_tasks(OSS_MODULES if modules is None else modules)


def lane_for_task(task_name: str) -> Lane:
    """Lane a task runs on; unregistered names default to interactive.

    Defaulting to interactive preserves pre-lane behaviour for anything not
    explicitly moved, so forgetting to annotate a task degrades to "as before"
    rather than to a silently unconsumed queue.
    """
    ensure_task_lanes_registered()
    return TASK_LANES.get(task_name, Lane.INTERACTIVE)


# How long the non-primary lanes get to unwind once the primary has shut down.
# The primary has already served its own grace period by this point, so the
# remaining lanes are only closing connections. Short, because the alternative
# to giving up is being SIGKILLed by the platform a moment later.
_SECONDARY_LANE_SHUTDOWN_SECONDS = 10.0

# Per-step ceiling for the primary lane's teardown. Closing a pool should take
# milliseconds; anything that takes seconds is wedged, and waiting on it only
# trades a clean exit for a SIGKILL.
_SHUTDOWN_STEP_TIMEOUT_SECONDS = 5.0

JOB_TIMEOUT_SECONDS = 1800
# An agent run is the one task whose ceiling is not ours to pick freely: it
# advertises a deadline to something outside this process (an Agent Host on a
# user's machine) and hands it a credential that expires. If the task dies
# first, Lemma reports the run failed while the remote agent keeps executing
# tools for it. This must therefore stay strictly above the Agent Host run
# window (DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS, 50 min) with enough margin
# for the harness to cancel the host run and finalize, and strictly below the
# one-hour validity of the MCP credential minted at dispatch.
AGENT_RUN_JOB_TIMEOUT_SECONDS = 3300
JOB_MAX_RETRIES = 3
# Keep completed task metadata around long enough for the UI to be useful.
JOB_RESULT_TTL_SECONDS = 60 * 60 * 24
WORKER_CONCURRENCY = settings.worker_concurrency


broker = RedisBroker(
    settings.redis_url,
    logger=get_dependency_logger("faststream.redis"),
    # FastStream uses this as the severity for routine startup narration. Keep
    # it at INFO and let the supplied WARNING logger drop those records while
    # still forwarding explicitly actionable warning/error calls.
    log_level=logging.INFO,
)


@dataclass(slots=True)
class AppWorkerContext:
    """Typed dependencies shared by streaq jobs."""

    job_queue: SharedStreaqJobQueue
    uow_factory: SessionUnitOfWorkFactory

    def uow(self):
        return self.uow_factory()

    def build_function_storage_factory(self):
        from app.modules.function.api.dependencies import (
            get_function_storage_factory,
        )

        return get_function_storage_factory()

    def build_function_service(self, uow: SqlAlchemyUnitOfWork):
        from app.core.infrastructure.events.message_bus import get_message_bus
        from app.modules.function.infrastructure.repositories import (
            FunctionRepository,
            FunctionRunRepository,
        )
        from app.modules.function.services.function_service import FunctionService

        message_bus = get_message_bus()
        return FunctionService(
            function_repository=FunctionRepository(uow, message_bus=message_bus),
            run_repository=FunctionRunRepository(uow, message_bus=message_bus),
            storage_factory=self.build_function_storage_factory(),
        )

    def build_function_use_cases(self):
        """Build the function use-case layer for the worker (same object the API
        builds). Used to execute queued runs without holding a pooled connection
        across the sandbox round-trip."""
        from app.modules.function.api.dependencies import build_function_use_cases

        return build_function_use_cases(self.uow_factory)

    def build_surface_event_handler(self, uow: SqlAlchemyUnitOfWork):
        from app.modules.agent.api.dependencies import get_conversation_service
        from app.modules.agent_surfaces.api.dependencies import (
            surface_repository_factory,
        )
        from app.modules.connectors.api.dependencies import (
            get_connector_service,
        )
        from app.modules.agent_surfaces.services.ingress_service import (
            AgentSurfaceIngressService,
        )
        from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
            SqlAlchemySurfaceRoutingResolutionAdapter,
        )
        from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
            SurfaceConversationLinkRepository,
        )

        return AgentSurfaceIngressService(
            uow=uow,
            surface_repository=surface_repository_factory(uow),
            conversation_link_repository=SurfaceConversationLinkRepository(uow),
            conversation_service=get_conversation_service(uow),
            connector_service=get_connector_service(uow),
            pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
        )

    def build_surface_event_handler_with_factory(self):
        """Build an AgentSurfaceIngressService that scopes its own short UoWs.

        Used by the process_surface_message worker task: execute_chat runs long
        external I/O (platform APIs, file ingest, voice transcription) that must
        NOT hold a pooled DB connection. The service resolves credentials and
        writes the inbound message in separate short UoWs from this factory.
        """
        from app.modules.agent.api.dependencies import get_conversation_service
        from app.modules.connectors.api.dependencies import get_connector_service
        from app.modules.agent_surfaces.services.ingress_service import (
            AgentSurfaceIngressService,
        )

        return AgentSurfaceIngressService(
            uow_factory=self.uow_factory,
            conversation_service_factory=get_conversation_service,
            connector_service_factory=get_connector_service,
        )


async def _safe_shutdown_step(name: str, fn: Callable[[], Awaitable[None]]) -> None:
    """Run one teardown step, bounded, and say which one is running.

    Both properties exist because of the same incident: a worker that stopped
    responding to SIGTERM left a log ending at `service.started`, so there was
    nothing to say which step had stalled — and one stalled step was enough to
    hold the whole process until the platform killed it. A step that cannot
    finish in time is now abandoned so the rest still run.
    """
    logger.debug(
        "infrastructure.streaq_runtime.worker_shutdown_step.diagnostic", step=name
    )
    try:
        await asyncio.wait_for(fn(), timeout=_SHUTDOWN_STEP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning(
            "infrastructure.streaq_runtime.worker_shutdown_step_timed_out.degraded",
            step=name,
            timeout_seconds=_SHUTDOWN_STEP_TIMEOUT_SECONDS,
        )
    except Exception:  # pragma: no cover
        logger.debug(
            "infrastructure.streaq_runtime.worker_shutdown_step.diagnostic", step=name
        )


async def _ensure_consumer_groups_once() -> None:
    """Create every registered Redis consumer group once, before broker start.

    Closes the broker-start race where a subscriber polls a not-yet-created
    group, gets NOGROUP, and stops permanently. Idempotent (BUSYGROUP is a
    no-op) and never raises — group plumbing must not block worker startup.
    """
    from app.core.infrastructure.events.stream_subscriber import (
        ensure_consumer_groups,
        registered_stream_groups,
    )

    # FastStream and streaq speak raw bytes, so this shares the
    # decode_responses=False pool rather than the application one.
    client = get_redis(decode_responses=False, blocking=True)
    try:
        len(registered_stream_groups())
        await ensure_consumer_groups(client, warn_on_create=False)
    except Exception:  # pragma: no cover - defensive
        logger.debug(
            "infrastructure.streaq_runtime.initial_consumer_group_ensure.diagnostic"
        )


async def _consumer_group_reconcile_loop() -> None:
    """Periodically re-ensure Redis consumer groups exist.

    Self-heals the FastStream supervisor retry-storm: if a consumer group is lost
    (flush / failover / eviction / trim), the subscriber's consume loop spins on
    NOGROUP forever. Recreating the group lets the next retry succeed and the
    subscriber resume — no manual restart. Cheap (one Redis connection, a handful
    of idempotent XGROUP CREATE calls per tick).
    """
    from app.core.infrastructure.events.config import event_transport_settings
    from app.core.infrastructure.events.stream_subscriber import ensure_consumer_groups

    interval = event_transport_settings.consumer_group_reconcile_interval_seconds
    client = get_redis(decode_responses=False, blocking=True)
    try:
        while True:
            try:
                await ensure_consumer_groups(client)
            except Exception:  # pragma: no cover - defensive
                logger.debug(
                    "infrastructure.streaq_runtime.consumer_group_reconcile.diagnostic"
                )
            await asyncio.sleep(interval)
    finally:
        await client.aclose()


# Low-rate structured heartbeat for remote absence detection. At 5 min this is
# <600 records/48h. service.version is attached by the logging context.
_WORKER_HEARTBEAT_INTERVAL_SECONDS = 300.0


async def _worker_heartbeat_loop() -> None:
    """Emit ``worker.heartbeat`` every 5 min while the worker loop is healthy."""
    while True:
        await asyncio.sleep(_WORKER_HEARTBEAT_INTERVAL_SECONDS)
        logger.info("worker.heartbeat")


@asynccontextmanager
async def worker_lifespan() -> AsyncGenerator[AppWorkerContext]:
    setup_logging(
        settings.environment,
        service_name="lemma-worker",
        json_logs=settings.json_logs_enabled,
        log_level=settings.log_level,
    )
    validate_release_identity(settings.environment)
    init_telemetry(service_name="lemma-worker")
    instrument_database_engine(get_engine())
    # Size the thread-offload pool before any task runs blocking work off-loop.
    from app.core.analytics.bootstrap import start_analytics, stop_analytics
    from app.core.concurrency.offload import configure_thread_pool
    from app.core.net.http_client import close_shared_http_client
    from app.core.observability.connection_scope import (
        start_connection_scope_monitor_from_settings,
    )

    configure_thread_pool()
    start_connection_scope_monitor_from_settings(service_name="lemma-worker")
    # The analytics consumer runs *here*, in the worker -- not in the API. Without
    # this the process-wide sink stays the import-time NullSink and every
    # product-analytics event is discarded, key or no key. Installs a null sink
    # unless ANALYTICS_WRITE_KEY is set, so a self-hosted or Desktop-local worker
    # still reports nothing.
    start_analytics()

    # There used to be a guardrail here requiring worker concurrency to fit
    # inside the DB pool, on the theory that a task holds a pooled connection
    # for its whole lifetime. It doesn't: every task takes a session per unit of
    # work and gives it back before any LLM call, HTTP request, sandbox
    # operation or thread offload — `make lint-session-scope` fails the build if
    # that stops being true. So concurrency is bounded by the pod's RAM and CPU,
    # not by the pool, and the two knobs are independent. Real pool pressure is
    # reported from measurement instead: the `database_pool_capacity` incident
    # in app/core/infrastructure/db/session.py fires on sustained checkout
    # saturation, which is the signal that actually means something.
    #
    # Pre-create Redis consumer groups BEFORE the broker starts its subscribers.
    # Several subscribers share a stream (e.g. workflow + surface both consume
    # `schedule_events`); at broker.start FastStream races to create each group,
    # and any subscriber that polls before its group exists gets NOGROUP and
    # stops permanently — the reconcile loop cannot revive a stopped subscriber.
    # Pre-creating closes that race so every subscriber attaches to a live group.
    await _ensure_consumer_groups_once()
    await broker.start()
    await channel_service.connect()
    job_queue = get_streaq_job_queue()
    await job_queue.connect()
    await get_message_bus().connect()
    initialize_supertokens()
    context = AppWorkerContext(
        job_queue=job_queue,
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
    )
    # Imported lazily to avoid an import cycle: the registry imports module
    # `module.py` files whose worker hooks reference AppWorkerContext (defined
    # in this file).
    from app.core.registry.assembly import enter_worker_lifespans
    from app.core.registry.installed import OSS_MODULES

    reconcile_task: asyncio.Task[None] | None = None
    from app.core.infrastructure.events.config import event_transport_settings

    if event_transport_settings.consumer_group_reconcile_interval_seconds > 0:
        reconcile_task = create_background_task(
            _consumer_group_reconcile_loop(), name="consumer-group-reconcile"
        )

    # Loop-lag watchdog: measures event-loop lag and refreshes the liveness
    # heartbeat the k8s probe reads, so a wedged worker gets restarted instead of
    # hanging silently (the worker has no HTTP server for a /livez probe).
    from app.core.observability.loop_watchdog import loop_lag_watchdog

    watchdog_task = create_background_task(
        loop_lag_watchdog(
            service_name="lemma-worker",
            heartbeat_path=settings.worker_heartbeat_path or None,
        ),
        name="worker-loop-lag-watchdog",
    )
    # Low-rate structured heartbeat for remote absence detection of this
    # singleton background process. At 5 min this is <600 records/48h. The
    # worker has no HTTP server, so the heartbeat event + the watchdog's
    # heartbeat file are its liveness signals.
    heartbeat_task = create_background_task(
        _worker_heartbeat_loop(), name="worker-heartbeat"
    )
    stream_snapshot_task = create_background_task(
        redis_stream_snapshot_loop(get_message_bus()),
        name="redis-stream-snapshot",
    )
    # Runs on the worker only: it is the process that owns the queues, and one
    # sampler is enough -- lane depth and pending-row counts are properties of
    # the shared Redis and database, not of the sampling process.
    backlog_gauge_task = create_background_task(
        backlog_gauge_loop(
            async_session_maker,
            interval_seconds=settings.backlog_gauge_interval_seconds,
        ),
        name="backlog-gauges",
    )

    # Fires due schedules and timers. Runs on every worker replica: the poll
    # claims with FOR UPDATE SKIP LOCKED, so replicas share the work rather than
    # duplicating it, and there is no leader to lose.
    from app.modules.agent.services.due_snooze_claimer import claim_due_snooze_waits
    from app.modules.schedule.services.schedule_poller import run_schedule_poller
    from app.modules.workflow.services.due_wait_claimer import (
        claim_due_workflow_waits,
    )

    schedule_poller_task = create_background_task(
        run_schedule_poller(
            context.uow_factory,
            # Injected here, where crossing module boundaries is the job.
            timer_claimers=(claim_due_workflow_waits, claim_due_snooze_waits),
            interval_seconds=settings.schedule_poll_interval_seconds,
        ),
        name="schedule-poller",
    )

    started = False
    global _primary_lane_context
    try:
        # Module-contributed worker lifespans (e.g. agent_surfaces native event
        # receiver + dedupe-store close; datastore reindex-queue close). Entered
        # after core startup and unwound before the core closers below.
        async with AsyncExitStack() as module_stack:
            await module_stack.enter_async_context(
                outbox_dispatcher_lifespan(async_session_maker, get_message_bus())
            )
            await enter_worker_lifespans(module_stack, OSS_MODULES, context)
            # Emit only after every core and module lifespan has entered.
            logger.info("service.started")
            started = True
            # Release any secondary lanes only now that the shared broker,
            # engine and module lifespans are fully up — they share this exact
            # context object and must not consume jobs before it is complete.
            _primary_lane_context = context
            _primary_lane_ready.set()
            yield context
    finally:
        # Before anything shared is disposed. Every lane runs on this one
        # context — the same engine, broker and Redis clients — so tearing them
        # down while a secondary lane is still consuming jobs is what used to
        # hang the process: `close_engine` and `broker.stop` wait on work that
        # nothing has told to stop. Stopping the other lanes first is the
        # ordering that makes the rest of this block finite.
        await _stop_secondary_lanes()
        _primary_lane_ready.clear()
        _primary_lane_context = None
        for background_task in (
            reconcile_task,
            watchdog_task,
            heartbeat_task,
            stream_snapshot_task,
            backlog_gauge_task,
            schedule_poller_task,
        ):
            if background_task is not None and not background_task.done():
                background_task.cancel()
                try:
                    await background_task
                except BaseException:
                    pass
        await _safe_shutdown_step("broker.stop", broker.stop)
        # After the broker, because the analytics consumer is what produces
        # these events -- draining a buffer that has stopped growing is the only
        # way the drain terminates. Before the HTTP client, which the sink posts
        # through.
        await _safe_shutdown_step("stop_analytics", stop_analytics)
        await _safe_shutdown_step(
            "close_shared_http_client", close_shared_http_client
        )
        await _safe_shutdown_step("close_streaq_job_queue", close_streaq_job_queue)
        await _safe_shutdown_step("close_message_bus", close_message_bus)
        await _safe_shutdown_step("close_redis_json_caches", close_redis_json_caches)
        await _safe_shutdown_step("close_redis_clients", close_redis_clients)
        await _safe_shutdown_step("close_engine", close_engine)
        await _safe_shutdown_step(
            "channel_service.disconnect", channel_service.disconnect
        )

        from app.modules.datastore.infrastructure.session import close_datastore_engine

        await _safe_shutdown_step("close_datastore_engine", close_datastore_engine)
        if started:
            logger.info("service.stopped")
        shutdown_telemetry()


@asynccontextmanager
async def secondary_lane_lifespan() -> AsyncGenerator[AppWorkerContext]:
    """Lifespan for every lane except the primary.

    ``worker_lifespan`` performs process-wide setup — telemetry, the DB engine,
    the FastStream broker and its consumer groups, the loop watchdog, the outbox
    dispatcher. Running it once per lane would start two brokers and two
    watchdogs in one process. So the primary lane owns all of it and publishes
    the resulting context here; secondary lanes just wait for it and share it.

    Lanes run concurrently in one event loop, so a plain asyncio.Event is the
    right handshake. If the primary never comes up, the wait fails loudly rather
    than letting a lane consume jobs with a half-built context.
    """
    await asyncio.wait_for(
        _primary_lane_ready.wait(),
        timeout=_SECONDARY_LANE_STARTUP_TIMEOUT_SECONDS,
    )
    context = _primary_lane_context
    if context is None:  # pragma: no cover — defensive
        raise RuntimeError("primary worker lane did not publish a context")
    yield context


def create_streaq_worker(
    *,
    handle_signals: bool,
    lane: Lane = Lane.INTERACTIVE,
    concurrency: int | None = None,
) -> Worker[AppWorkerContext]:
    return Worker(
        redis_url=settings.redis_url,
        queue_name=lane_queue_name(lane),
        concurrency=concurrency if concurrency is not None else lane_concurrency(lane),
        # Only the primary lane should watch for signals: streaq's handler
        # cancels just its OWN worker's scope, so a handler per lane means a
        # SIGTERM stops one lane and leaves the others running, and the process
        # never exits.
        #
        # This flag does not achieve that. streaq stores `handle_signals` and
        # never reads it — `run_async` starts `signal_handler` unconditionally
        # — so every lane opens a receiver for SIGINT/SIGTERM. asyncio's
        # `add_signal_handler` is last-wins, so which lane actually receives the
        # signal is a startup race: about one time in four the bulk lane won,
        # cancelled only itself, and the worker hung until it was SIGKILLed.
        # `_silence_lane_signal_handler` below is what really enforces this.
        handle_signals=handle_signals and lane is _PRIMARY_LANE,
        lifespan=(
            worker_lifespan if lane is _PRIMARY_LANE else secondary_lane_lifespan
        ),
        # On SIGTERM, give in-flight tasks this long to finish before forcing
        # cancellation. Lets an interrupted agent run finalize its status in the
        # DB (via the shielded finalization in AgentRunnerService.execute) before
        # worker_lifespan's finally disposes the engine — otherwise the run can
        # be left stuck in RUNNING. Backstopped by reconcile_orphaned_agent_runs.
        grace_period=settings.worker_shutdown_grace_period_seconds,
    )


# One Worker per lane. ``streaq_worker`` stays the name of the interactive lane
# so the ``streaq run app.events:streaq_worker`` entrypoint and every existing
# ``streaq_worker.context`` read keep working — streaq stores the running
# context in a MODULE-level ContextVar, so that accessor resolves correctly no
# matter which lane is executing the task.
streaq_worker = create_streaq_worker(handle_signals=True, lane=Lane.INTERACTIVE)
bulk_worker = create_streaq_worker(handle_signals=True, lane=Lane.BULK)

LANE_WORKERS: dict[Lane, Worker[AppWorkerContext]] = {
    Lane.INTERACTIVE: streaq_worker,
    Lane.BULK: bulk_worker,
}

for _lane, _worker in LANE_WORKERS.items():
    if _lane is not _PRIMARY_LANE:
        _silence_lane_signal_handler(_worker)


def enabled_lanes() -> list[Lane]:
    """Lanes this process should consume, from ``WORKER_LANES``.

    Defaults to every lane so a single-process deployment (local stack, desktop,
    today's cloud worker) keeps behaving exactly as before. Split deployments set
    WORKER_LANES=interactive on one and WORKER_LANES=bulk on the other.
    """
    raw = (settings.worker_lanes or "").strip()
    if not raw:
        return list(Lane)
    seen: list[Lane] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        try:
            lane = Lane(name)
        except ValueError:
            raise ValueError(
                f"WORKER_LANES contains unknown lane {name!r}; "
                f"valid lanes are {', '.join(x.value for x in Lane)}"
            ) from None
        if lane not in seen:
            seen.append(lane)
    if not seen:
        return list(Lane)
    # The primary lane owns the shared lifespan, so it must start first.
    seen.sort(key=lambda lane: 0 if lane is _PRIMARY_LANE else 1)
    return seen


async def run_worker_lanes(lanes: Sequence[Lane] | None = None) -> None:
    """Run the selected lanes concurrently in this process.

    Each lane is an independent streaq Worker on its own Redis queue with its own
    concurrency budget, which is the whole point: a burst of bulk ingestion can
    no longer occupy the slots that agent runs and surface messages need.
    """
    selected = list(lanes) if lanes is not None else enabled_lanes()
    if _PRIMARY_LANE not in selected:
        # Something has to own the shared lifespan (broker, engine, watchdog).
        raise ValueError(
            f"the {_PRIMARY_LANE.value} lane owns process-wide startup and must be "
            f"enabled; got {[lane.value for lane in selected]}"
        )
    logger.info(
        "worker.lanes.starting",
        lanes=",".join(lane.value for lane in selected),
    )
    _install_task_dump_handler()
    primary, *secondary = selected
    if not secondary:
        await LANE_WORKERS[primary].run_async()
        return

    # The primary lane owns signal handling and the shared lifespan, so its
    # return is the process's shutdown signal: wait for it to unwind gracefully,
    # then stop the remaining lanes. Cancelling a bulk extraction mid-flight is
    # safe — the row stays PROCESSING and the recovery cron reclaims it.
    #
    # Plain tasks rather than a task group, because leaving is not optional.
    # `async with create_task_group()` waits for its children unconditionally,
    # and a streaq worker does not always unwind promptly when cancelled: parts
    # of its shutdown run under a shielded scope, so an external cancel can be
    # held off until an in-flight Redis call returns. Measured on a SIGTERM
    # delivered mid-run, that hung the whole process about one time in four —
    # and a worker that does not exit gets SIGKILLed by the platform, which
    # takes the in-flight agent run's finalization with it.
    _secondary_lane_tasks.clear()
    _secondary_lane_tasks.extend(
        create_background_task(
            LANE_WORKERS[lane].run_async(), name=f"worker-lane-{lane.value}"
        )
        for lane in secondary
    )
    try:
        await LANE_WORKERS[primary].run_async()
    finally:
        # Normally already done, from inside the primary's lifespan teardown.
        # Repeated here for the paths that never reach it — a primary that
        # fails during startup still has to take the other lanes with it.
        await _stop_secondary_lanes()


async def load_job_observability_context(redis, job_id: str) -> dict[str, str]:
    """Best-effort read of the rolling-deployment-compatible sidecar."""
    try:
        raw = await redis.get(job_context_key(job_id))
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, str | int)
        }
    except Exception:
        return {}


def _register_observability_middleware(
    worker: Worker[AppWorkerContext],
) -> None:
    """Attach the tracing/metrics wrapper to one lane's worker.

    Built per worker rather than shared, because streaq exposes the running task
    on the object returned by ``Worker.middleware()`` — not on the function that
    was passed in. Registering one shared function across lanes and discarding
    those return values leaves the closure with no way to reach the current task.
    """

    def observability_context_middleware(call_next):
        """Recover correlation stored beside a task without changing its payload."""

        async def run(*args, **kwargs):
            task = registered.context
            inherited = await load_job_observability_context(
                worker.redis, task.task_id
            )
            token = otel_context.attach(extract(inherited))
            started_at = time.perf_counter()
            outcome = "succeeded"
            try:
                with tracer.start_as_current_span(
                    "lemma.worker.job",
                    kind=SpanKind.CONSUMER,
                    attributes={
                        "lemma.job_id": task.task_id,
                        "lemma.task_name": task.fn_name,
                        "lemma.attempt": task.tries,
                    },
                ) as span:
                    with bind_job_context(
                        job_id=task.task_id,
                        task_name=task.fn_name,
                        attempt=task.tries,
                        inherited=inherited,
                    ):
                        try:
                            result = await call_next(*args, **kwargs)
                            span.set_attribute("lemma.outcome", outcome)
                            return result
                        except asyncio.CancelledError:
                            outcome = "cancelled"
                            span.set_attribute("lemma.outcome", outcome)
                            raise
                        except Exception as exc:
                            terminal = task.tries >= JOB_MAX_RETRIES
                            outcome = "failed" if terminal else "retrying"
                            span.set_attribute("lemma.outcome", outcome)
                            duration_ms = round(
                                (time.perf_counter() - started_at) * 1000, 1
                            )
                            if terminal:
                                logger.error(
                                    "worker.job.failed",
                                    attempt=task.tries,
                                    retryable=False,
                                    duration_ms=duration_ms,
                                    error_type=type(exc).__name__,
                                    exc_info=True,
                                )
                            else:
                                logger.debug(
                                    "worker.job.retrying",
                                    attempt=task.tries,
                                    retryable=True,
                                    error_type=type(exc).__name__,
                                )
                            raise
            finally:
                duration_ms = (time.perf_counter() - started_at) * 1000
                labels = {"task_name": task.fn_name, "outcome": outcome}
                job_counter.add(1, labels)
                job_duration.record(duration_ms, labels)
                otel_context.detach(token)

        return run

    # `registered` is what exposes the running task to the closure above; it is
    # bound before any task runs, so the late reference inside `run` is safe.
    registered = worker.middleware(observability_context_middleware)


# Every lane gets the same observability wrapper — a job must be traced the same
# way regardless of which queue carried it.
for _lane_worker in LANE_WORKERS.values():
    _register_observability_middleware(_lane_worker)


def _register_lane(name: str | None, lane: Lane) -> None:
    if name:
        TASK_LANES[name] = lane


def streaq_task(*args, lane: Lane = Lane.INTERACTIVE, **kwargs):
    """Register a task on ``lane``'s worker.

    A task is registered on exactly one lane's Worker, so it is consumed from
    exactly one queue and can never be picked up twice.
    """
    kwargs.setdefault("max_tries", JOB_MAX_RETRIES)
    kwargs.setdefault("timeout", JOB_TIMEOUT_SECONDS)
    kwargs.setdefault("ttl", JOB_RESULT_TTL_SECONDS)
    _register_lane(kwargs.get("name"), lane)
    return LANE_WORKERS[lane].task(*args, **kwargs)


def streaq_cron(tab: str, *, lane: Lane = Lane.INTERACTIVE, **kwargs):
    """Register a cron on ``lane``'s worker.

    Registering on one lane is load-bearing: with several lanes running in the
    same process, a cron registered on more than one Worker would fire once per
    lane on every tick.
    """
    kwargs.setdefault("max_tries", JOB_MAX_RETRIES)
    kwargs.setdefault("timeout", JOB_TIMEOUT_SECONDS)
    kwargs.setdefault("ttl", JOB_RESULT_TTL_SECONDS)
    _register_lane(kwargs.get("name"), lane)
    return LANE_WORKERS[lane].cron(tab, **kwargs)
