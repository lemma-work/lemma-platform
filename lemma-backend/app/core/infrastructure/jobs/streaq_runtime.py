"""Shared streaq worker runtime and dependency context."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from faststream.redis import RedisBroker
from streaq import Worker

from app.core.config import settings
from app.core.infrastructure.channels.channel_service import channel_service
from app.core.infrastructure.db.session import async_session_maker, get_engine, close_engine
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.events.message_bus import close_message_bus, get_message_bus
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    close_streaq_job_queue,
    get_streaq_job_queue,
)
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.core.log.log import get_logger, setup_logging
from app.core.observability.telemetry import init_telemetry, instrument_database_engine

logger = get_logger(__name__)

JOB_TIMEOUT_SECONDS = 1800
JOB_MAX_RETRIES = 3
# Keep completed task metadata around long enough for the UI to be useful.
JOB_RESULT_TTL_SECONDS = 60 * 60 * 24
WORKER_CONCURRENCY = settings.worker_concurrency


broker = RedisBroker(settings.redis_url)


@dataclass(slots=True)
class AppWorkerContext:
    """Typed dependencies shared by streaq jobs."""

    job_queue: SharedStreaqJobQueue
    uow_factory: SessionUnitOfWorkFactory

    def uow(self):
        return self.uow_factory()

    def build_function_storage_factory(self):
        from app.modules.function.services.function_file_manager import FunctionFileManager

        if settings.effective_storage_backend() == "gcs":
            if not settings.gcs_storage_bucket:
                raise ValueError("GCS storage requires GCS_STORAGE_BUCKET")
            return partial(FunctionFileManager, bucket_name=settings.gcs_storage_bucket)
        return partial(
            FunctionFileManager,
            root_path=Path(settings.local_file_storage_root) / "common",
        )

    def build_function_service(self, uow: SqlAlchemyUnitOfWork):
        from app.core.infrastructure.events.message_bus import get_message_bus
        from app.modules.function.infrastructure.repositories import (
            FunctionRepository,
            FunctionRunRepository,
        )
        from app.modules.function.services.function_service import FunctionService
        from app.modules.pod.services.authorization_factory import create_authorization_service
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

    def build_function_service_with_factory(self):
        """Build a FunctionService that uses uow_factory for scoped DB sessions.

        Unlike ``build_function_service(uow)``, this does not hold a DB session
        for the service's entire lifetime. The service opens short UoWs around
        each DB operation, releasing connections between them — critical for
        long-running tasks (e.g. function execution) that must not hold pooled
        connections idle during external I/O.
        """
        from app.modules.function.services.function_service import FunctionService
        from app.modules.workspace.services.workspace_tool_runtime import (
            get_function_workspace_runtime,
        )

        return FunctionService(
            function_repository=None,
            run_repository=None,
            workspace_service=get_function_workspace_runtime(),
            storage_factory=self.build_function_storage_factory(),
            job_queue=self.job_queue,
            authorization_service=None,
            uow_factory=self.uow_factory,
        )

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


async def _safe_shutdown_step(
    name: str, fn: Callable[[], Awaitable[None]]
) -> None:
    try:
        await fn()
    except Exception as exc:  # pragma: no cover
        logger.warning("Worker shutdown step failed", step=name, error=str(exc))


async def _consumer_group_reconcile_loop() -> None:
    """Periodically re-ensure Redis consumer groups exist.

    Self-heals the FastStream supervisor retry-storm: if a consumer group is lost
    (flush / failover / eviction / trim), the subscriber's consume loop spins on
    NOGROUP forever. Recreating the group lets the next retry succeed and the
    subscriber resume — no manual restart. Cheap (one Redis connection, a handful
    of idempotent XGROUP CREATE calls per tick).
    """
    import redis.asyncio as redis

    from app.core.infrastructure.events.stream_subscriber import ensure_consumer_groups

    interval = settings.consumer_group_reconcile_interval_seconds
    client = redis.from_url(settings.redis_url, decode_responses=False)
    try:
        while True:
            try:
                await ensure_consumer_groups(client)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Consumer group reconcile failed", error=str(exc))
            await asyncio.sleep(interval)
    finally:
        await client.aclose()


@asynccontextmanager
async def worker_lifespan() -> AsyncGenerator[AppWorkerContext]:
    setup_logging(
        settings.environment,
        service_name="gappy-worker",
        json_logs=settings.json_logs_enabled,
        log_level=settings.log_level,
    )
    init_telemetry(service_name="gappy-worker")
    instrument_database_engine(get_engine())
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
    logger.info("Worker starting...")
    # Imported lazily to avoid an import cycle: the registry imports module
    # `module.py` files whose worker hooks reference AppWorkerContext (defined
    # in this file).
    from app.core.registry.assembly import enter_worker_lifespans
    from app.core.registry.installed import OSS_MODULES

    reconcile_task: asyncio.Task[None] | None = None
    if settings.consumer_group_reconcile_interval_seconds > 0:
        reconcile_task = asyncio.create_task(_consumer_group_reconcile_loop())

    try:
        # Module-contributed worker lifespans (e.g. agent_surfaces native event
        # receiver + dedupe-store close; datastore reindex-queue close). Entered
        # after core startup and unwound before the core closers below.
        async with AsyncExitStack() as module_stack:
            await enter_worker_lifespans(module_stack, OSS_MODULES, context)
            yield context
    finally:
        if reconcile_task is not None and not reconcile_task.done():
            reconcile_task.cancel()
            try:
                await reconcile_task
            except BaseException:
                pass
        await _safe_shutdown_step("broker.stop", broker.stop)
        await _safe_shutdown_step("close_streaq_job_queue", close_streaq_job_queue)
        await _safe_shutdown_step("close_message_bus", close_message_bus)
        await _safe_shutdown_step("close_engine", close_engine)
        await _safe_shutdown_step("channel_service.disconnect", channel_service.disconnect)

        from app.modules.datastore.infrastructure.session import close_datastore_engine

        await _safe_shutdown_step("close_datastore_engine", close_datastore_engine)
        logger.info("Worker shutting down...")


def create_streaq_worker(*, handle_signals: bool) -> Worker[AppWorkerContext]:
    return Worker(
        redis_url=settings.redis_url,
        queue_name="default",
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
