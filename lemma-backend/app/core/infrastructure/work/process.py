"""The worker process: everything a process that runs the work runtime needs.

``worker_services(modules)`` is the one lifespan both hosts enter:

* ``python -m app.worker`` (``app/worker.py``), the standalone worker, which
  owns its signals: SIGTERM starts the runtime's drain at once
  (``WorkRuntime.begin_stop``), each class gets its own shutdown grace
  (``WORK_SHUTDOWN_GRACE_JSON``), work still running past it is released with
  nothing charged, and the process exits 0;
* the embedded single-process app (``app/standalone.py``: desktop, the local
  stack, compose), where uvicorn owns the signals and the lifespan's exit is
  the stop.

In order, it sets up telemetry and the process-wide pools, registers every
module's work kinds, facts, projections and periodics, starts the process
monitors (the loop-lag watchdog that writes the liveness heartbeat file, the
Redis liveness key the API's readiness reads, the memory sampler, a 5-minute
heartbeat log), enters the modules' worker lifespans, and starts the work
runtime last -- so it stops first, while everything its handlers use is still
open. ``service.started`` (logger ``app.core.infrastructure.work.process``) is
the readiness line the e2e harness waits for.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.infrastructure.db.session import (
    async_session_maker,
    close_engine,
    get_engine,
)
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

if TYPE_CHECKING:
    from app.core.infrastructure.work.runtime import WorkRuntime
    from app.core.registry.contract import LemmaModule

logger = get_logger(__name__)

#: The line (and logger) a host or a test waits for: the runtime is claiming.
READY_EVENT = "service.started"
READY_LOGGER = __name__

_HEARTBEAT_INTERVAL_SECONDS = 300.0
#: Each teardown step gets this long, so one stalled closer cannot hold the
#: process past the platform's kill.
_SHUTDOWN_STEP_TIMEOUT_SECONDS = 10.0
#: What a closer may fail with and the rest of the teardown still run.
_CLOSE_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    RuntimeError,
    SQLAlchemyError,
)


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """What a module's worker lifespan is given."""

    uow_factory: UnitOfWorkFactory

    def uow(self):
        return self.uow_factory()


def register_worker_modules(modules: Sequence[LemmaModule]) -> None:
    """Everything the modules register in a worker. Idempotent (imports)."""
    from app.core.registry.assembly import (
        configure_namespace_directory,
        configure_plan_limits,
        configure_pod_liveness,
        configure_resource_names,
        register_module_periodic_work,
    )

    configure_resource_names(modules)
    configure_pod_liveness(modules)
    configure_plan_limits(modules)
    configure_namespace_directory(modules)
    register_module_periodic_work(modules)


async def _heartbeat_log() -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        logger.info("worker.heartbeat")


async def _close_step(name: str, close: Callable[[], Awaitable[object]]) -> None:
    try:
        await asyncio.wait_for(close(), timeout=_SHUTDOWN_STEP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("worker.shutdown_step.timed_out", step=name)
    except _CLOSE_ERRORS:
        logger.warning("worker.shutdown_step.failed", step=name, exc_info=True)


def _monitors(service_name: str, *, embedded: bool) -> list[asyncio.Task[None]]:
    from app.core.infrastructure.redis.client import get_redis
    from app.core.observability.loop_watchdog import loop_lag_watchdog
    from app.core.observability.memory_sampler import memory_sampler
    from app.core.observability.worker_liveness import worker_liveness_loop

    # The same signal where the API's /health/ready can read it.
    tasks = [
        create_background_task(
            worker_liveness_loop(get_redis()), name="worker-liveness"
        )
    ]
    if embedded:
        # The API process already runs its own loop watchdog and sampler.
        return tasks
    return [
        *tasks,
        # Refreshes the heartbeat file the k8s probe reads, so a wedged loop
        # gets the process restarted rather than hanging silently.
        create_background_task(
            loop_lag_watchdog(
                service_name=service_name,
                heartbeat_path=settings.worker_heartbeat_path or None,
            ),
            name="worker-loop-lag-watchdog",
        ),
        create_background_task(
            memory_sampler(service_name=service_name), name="worker-memory-sampler"
        ),
        create_background_task(_heartbeat_log(), name="worker-heartbeat"),
    ]


@asynccontextmanager
async def worker_services(
    modules: Sequence[LemmaModule],
    *,
    service_name: str = "lemma-worker",
    embedded: bool = False,
) -> AsyncIterator[WorkRuntime]:
    """Run the work runtime and its process services for the block.

    ``embedded``: inside the API process, which already owns telemetry, the
    analytics sink, the shared clients and the engine -- so this neither
    starts nor closes them.
    """
    from app.core.infrastructure.work.runtime import work_runtime_lifespan
    from app.core.observability.startup_timing import (
        finish_startup,
        release_startup_heap,
    )
    from app.core.observability.task_dump import install_task_dump_handler
    from app.core.registry.assembly import enter_worker_lifespans

    boot_started = time.monotonic()
    if not embedded:
        await _start_process_services(service_name)
    register_worker_modules(modules)
    install_task_dump_handler()
    monitors = _monitors(service_name, embedded=embedded)
    started = False
    try:
        async with AsyncExitStack() as stack:
            await enter_worker_lifespans(
                stack,
                modules,
                WorkerContext(SessionUnitOfWorkFactory(async_session_maker)),
            )
            runtime = await stack.enter_async_context(
                work_runtime_lifespan(database_url=settings.database_url)
            )
            ms, frozen = finish_startup(boot_started)
            logger.info("service.started", startup_ms=ms, gc_frozen_objects=frozen)
            started = True
            yield runtime
    finally:
        for task in monitors:
            task.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        if not embedded:
            await _close_process_services()
        if started:
            logger.info("service.stopped")
            release_startup_heap()


async def _start_process_services(service_name: str) -> None:
    from app.core.analytics.bootstrap import start_analytics
    from app.core.infrastructure.channels.channel_service import channel_service
    from app.core.concurrency.offload import configure_thread_pool
    from app.core.observability.connection_scope import (
        start_connection_scope_monitor_from_settings,
    )
    from app.core.observability.telemetry import (
        init_telemetry,
        instrument_database_engine,
    )

    init_telemetry(service_name=service_name)
    instrument_database_engine(get_engine())
    configure_thread_pool()
    start_connection_scope_monitor_from_settings(service_name=service_name)
    # Without it the process-wide analytics sink stays the import-time null
    # sink; it is a null sink anyway unless ANALYTICS_WRITE_KEY is set.
    start_analytics()
    # Live frames (a run's progress) are published from handlers.
    await channel_service.connect()


async def _close_process_services() -> None:
    from app.core.analytics.bootstrap import stop_analytics
    from app.core.infrastructure.cache.redis_json_cache import (
        close_redis_json_caches,
    )
    from app.core.infrastructure.channels.channel_service import channel_service
    from app.core.infrastructure.redis.client import close_redis_clients
    from app.core.net.http_client import close_shared_http_client
    from app.core.net.impersonating_client import close_impersonating_client
    from app.core.observability.connection_scope import stop_connection_scope_monitor
    from app.core.observability.telemetry import shutdown_telemetry

    # The sink first: projections still use the HTTP client and the engine.
    await _close_step("stop_analytics", stop_analytics)
    await _close_step("close_shared_http_client", close_shared_http_client)
    await _close_step("close_impersonating_client", close_impersonating_client)
    await _close_step("close_redis_json_caches", close_redis_json_caches)
    stop_connection_scope_monitor()
    await _close_step("close_redis_clients", close_redis_clients)
    await _close_step("close_engine", close_engine)
    await _close_step("channel_service.disconnect", channel_service.disconnect)
    shutdown_telemetry()


__all__ = [
    "READY_EVENT",
    "READY_LOGGER",
    "WorkerContext",
    "register_worker_modules",
    "worker_services",
]
