"""Shared streaq worker runtime and dependency context."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from faststream.redis import RedisBroker
from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind
from streaq import Worker

from app.core.config import settings
from app.core.infrastructure.channels.channel_service import channel_service
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

JOB_TIMEOUT_SECONDS = 1800
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
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )
        from app.modules.workspace.services.workspace_tool_runtime import (
            get_function_workspace_runtime,
        )

        message_bus = get_message_bus()
        return FunctionService(
            function_repository=FunctionRepository(uow, message_bus=message_bus),
            run_repository=FunctionRunRepository(uow, message_bus=message_bus),
            workspace_service=get_function_workspace_runtime(),
            storage_factory=self.build_function_storage_factory(),
            job_queue=self.job_queue,
            authorization_service=create_authorization_service(uow),
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
    import redis.asyncio as redis

    from app.core.infrastructure.events.stream_subscriber import (
        ensure_consumer_groups,
        registered_stream_groups,
    )

    client = redis.from_url(settings.redis_url, decode_responses=False)
    try:
        len(registered_stream_groups())
        await ensure_consumer_groups(client, warn_on_create=False)
    except Exception:  # pragma: no cover - defensive
        logger.debug(
            "infrastructure.streaq_runtime.initial_consumer_group_ensure.diagnostic"
        )
    finally:
        await client.aclose()


async def _consumer_group_reconcile_loop() -> None:
    """Periodically re-ensure Redis consumer groups exist.

    Self-heals the FastStream supervisor retry-storm: if a consumer group is lost
    (flush / failover / eviction / trim), the subscriber's consume loop spins on
    NOGROUP forever. Recreating the group lets the next retry succeed and the
    subscriber resume — no manual restart. Cheap (one Redis connection, a handful
    of idempotent XGROUP CREATE calls per tick).
    """
    import redis.asyncio as redis

    from app.core.infrastructure.events.config import event_transport_settings
    from app.core.infrastructure.events.stream_subscriber import ensure_consumer_groups

    interval = event_transport_settings.consumer_group_reconcile_interval_seconds
    client = redis.from_url(settings.redis_url, decode_responses=False)
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
    pool_capacity = settings.db_pool_size + settings.db_max_overflow
    if pool_capacity and settings.worker_concurrency > pool_capacity * 0.8:
        logger.warning(
            "infrastructure.streaq_runtime.worker_concurrency_exceeds_safe_db.degraded"
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

    started = False
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
            yield context
    finally:
        for background_task in (reconcile_task, watchdog_task, heartbeat_task):
            if background_task is not None and not background_task.done():
                background_task.cancel()
                try:
                    await background_task
                except BaseException:
                    pass
        await _safe_shutdown_step("broker.stop", broker.stop)
        await _safe_shutdown_step("close_streaq_job_queue", close_streaq_job_queue)
        await _safe_shutdown_step("close_message_bus", close_message_bus)
        await _safe_shutdown_step("close_engine", close_engine)
        await _safe_shutdown_step(
            "channel_service.disconnect", channel_service.disconnect
        )

        from app.modules.datastore.infrastructure.session import close_datastore_engine

        await _safe_shutdown_step("close_datastore_engine", close_datastore_engine)
        if started:
            logger.info("service.stopped")
        shutdown_telemetry()


def create_streaq_worker(*, handle_signals: bool) -> Worker[AppWorkerContext]:
    return Worker(
        redis_url=settings.redis_url,
        queue_name=settings.worker_queue_name,
        concurrency=WORKER_CONCURRENCY,
        handle_signals=handle_signals,
        lifespan=worker_lifespan,
        # On SIGTERM, give in-flight tasks this long to finish before forcing
        # cancellation. Lets an interrupted agent run finalize its status in the
        # DB (via the shielded finalization in AgentRunnerService.execute) before
        # worker_lifespan's finally disposes the engine — otherwise the run can
        # be left stuck in RUNNING. Backstopped by reconcile_orphaned_agent_runs.
        grace_period=settings.worker_shutdown_grace_period_seconds,
    )


streaq_worker = create_streaq_worker(handle_signals=True)


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


@streaq_worker.middleware
def observability_context_middleware(call_next):
    """Recover correlation stored beside a task without changing its payload."""

    async def run(*args, **kwargs):
        task = observability_context_middleware.context
        inherited = await load_job_observability_context(
            streaq_worker.redis, task.task_id
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


def streaq_task(*args, **kwargs):
    kwargs.setdefault("max_tries", JOB_MAX_RETRIES)
    kwargs.setdefault("timeout", JOB_TIMEOUT_SECONDS)
    kwargs.setdefault("ttl", JOB_RESULT_TTL_SECONDS)
    return streaq_worker.task(*args, **kwargs)


def streaq_cron(tab: str, **kwargs):
    kwargs.setdefault("max_tries", JOB_MAX_RETRIES)
    kwargs.setdefault("timeout", JOB_TIMEOUT_SECONDS)
    kwargs.setdefault("ttl", JOB_RESULT_TTL_SECONDS)
    return streaq_worker.cron(tab, **kwargs)
