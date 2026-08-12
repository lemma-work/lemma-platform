"""Shared streaq worker runtime and dependency context."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

import anyio
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


def lane_for_task(task_name: str) -> Lane:
    """Lane a task runs on; unregistered names default to interactive.

    Defaulting to interactive preserves pre-lane behaviour for anything not
    explicitly moved, so forgetting to annotate a task degrades to "as before"
    rather than to a silently unconsumed queue.
    """
    return TASK_LANES.get(task_name, Lane.INTERACTIVE)


# Headroom between task concurrency and the DB pool, leaving room for the
# crons, event handlers and reconcilers that share the worker.
_DB_POOL_SAFETY_FACTOR = 0.8

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
    try:
        await fn()
    except Exception:  # pragma: no cover
        logger.debug("infrastructure.streaq_runtime.worker_shutdown_step.diagnostic")


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
    client = get_redis(decode_responses=False)
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
    client = get_redis(decode_responses=False)
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
    from app.core.concurrency.offload import configure_thread_pool

    configure_thread_pool()

    # Guardrail: each task that opens a DB session holds a pooled connection for
    # its duration, so concurrency above the pool capacity means tasks block on
    # connection checkout — which looks like the whole worker hanging. Warn (not
    # fail, to keep dev flexible) when the margin is too thin so it can't
    # silently regress.
    # The shipped defaults sit exactly on this line: concurrency 20 against a
    # pool of 20. `worker_concurrency`'s own docstring calls that acceptable
    # ("should not exceed"), but equal is not enough — the worker also runs
    # crons, event-bus handlers and reconcilers that each need a connection, so
    # at parity the first one of those blocks behind a full pool. Hence the 0.8
    # margin, and hence logging the numbers: a bare "degraded" event tells an
    # operator nothing about which of the two knobs to move.
    #
    # Concurrency is summed across the lanes this process actually runs: with
    # both enabled, interactive and bulk tasks draw from the same pool at the
    # same time, so their combined budget is what can exhaust it.
    pool_capacity = settings.db_pool_size + settings.db_max_overflow
    safe_concurrency = int(pool_capacity * _DB_POOL_SAFETY_FACTOR)
    configured_concurrency = sum(lane_concurrency(lane) for lane in enabled_lanes())
    if pool_capacity and configured_concurrency > safe_concurrency:
        logger.warning(
            "infrastructure.streaq_runtime.worker_concurrency_exceeds_safe_db.degraded",
            configured_concurrency=configured_concurrency,
            pool_capacity=pool_capacity,
            safe_concurrency=safe_concurrency,
        )
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
        _primary_lane_ready.clear()
        _primary_lane_context = None
        for background_task in (
            reconcile_task,
            watchdog_task,
            heartbeat_task,
            stream_snapshot_task,
        ):
            if background_task is not None and not background_task.done():
                background_task.cancel()
                try:
                    await background_task
                except BaseException:
                    pass
        await _safe_shutdown_step("broker.stop", broker.stop)
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
        # Only the primary lane watches for signals. streaq's handler cancels
        # just its OWN worker's scope, so with a handler per lane a SIGTERM
        # stops one lane and leaves the others running — the process then never
        # exits. The primary handles the signal (applying its grace period so an
        # in-flight agent run can still finalize) and run_worker_lanes stops the
        # rest once it unwinds.
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
    primary, *secondary = selected
    if not secondary:
        await LANE_WORKERS[primary].run_async()
        return

    # The primary lane owns signal handling and the shared lifespan, so its
    # return is the process's shutdown signal: wait for it to unwind gracefully,
    # then stop the remaining lanes. Cancelling a bulk extraction mid-flight is
    # safe — the row stays PROCESSING and the recovery cron reclaims it.
    async with anyio.create_task_group() as task_group:
        for lane in secondary:
            task_group.start_soon(LANE_WORKERS[lane].run_async)
        try:
            await LANE_WORKERS[primary].run_async()
        finally:
            task_group.cancel_scope.cancel()


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
