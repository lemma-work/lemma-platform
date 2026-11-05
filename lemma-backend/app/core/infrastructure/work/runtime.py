"""The work runtime in a process: a dispatcher per enabled class, one listener, upkeep loops.

Started from the shared worker lifespan, so ``python -m app.worker``, the
lemma-cloud worker and the embedded worker in ``app/standalone.py`` all run it
with no entrypoint change. Shutdown stops claiming first, lets running work
finish or reach a safe point within the grace, then releases whatever this
process still owns -- charging nothing.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transient_errors import is_database_unavailable
from app.core.infrastructure.work import maintenance
from app.core.infrastructure.work.config import (
    class_shutdown_grace,
    class_slots,
    enabled_classes,
    work_settings,
)
from app.core.infrastructure.work.db import (
    DbBudget,
    SessionMaker,
    WorkDb,
    check_fits,
)
from app.core.infrastructure.work.dispatcher import Dispatcher
from app.core.infrastructure.work.execute import ExecEnv
from app.core.infrastructure.work.gauges import gauge_loop
from app.core.infrastructure.work.limits import WorkLimitPort, current_work_limit_port
from app.core.infrastructure.work.listener import WorkWakeListener
from app.core.infrastructure.work.registry import (
    WorkRegistry,
    registry as default_registry,
)
from app.core.infrastructure.work.types import WorkClass
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

logger = get_logger(__name__)

#: How long a booting worker waits for a database that refuses connections.
_BOOT_DB_WAIT_SECONDS = 60.0


def process_owner() -> str:
    """A lease owner unique to this process incarnation, readable in pg_stat and the table."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def _engine_of(session_maker: SessionMaker) -> AsyncEngine | None:
    bind = getattr(session_maker, "kw", {}).get("bind")
    return bind if isinstance(bind, AsyncEngine) else None


class WorkRuntime:
    def __init__(
        self,
        session_maker: SessionMaker,
        *,
        classes: list[WorkClass],
        registry: WorkRegistry | None = None,
        database_url: str | None = None,
        limits: WorkLimitPort | None = None,
        owner: str | None = None,
        budget: DbBudget | None = None,
    ) -> None:
        self._registry = registry or default_registry
        self._database_url = database_url
        self._limits = limits
        self.budget = budget or DbBudget.from_settings(classes)
        # Refuse to start rather than discover under load that the pool cannot
        # hold what the slots may take: that surfaces as pool timeouts in
        # handlers, charged to whatever item happened to be waiting.
        check_fits(self.budget, _engine_of(session_maker))
        self.db = WorkDb(session_maker, self.budget)
        self.stopping = asyncio.Event()
        base = owner or process_owner()
        self.dispatchers = {
            cls: Dispatcher(
                cls,
                ExecEnv(
                    db=self.db,
                    registry=self._registry,
                    owner=f"{base}:{cls.value}",
                    statement_timeout_ms=work_settings.work_fast_statement_timeout_ms,
                    stopping=self.stopping,
                    local_wake=self._wake_local,
                ),
                slots=class_slots(cls),
                quantum=work_settings.work_quantum,
                poll_interval=work_settings.work_poll_interval_seconds,
            )
            for cls in classes
        }
        self._tasks: list[asyncio.Task[None]] = []
        self._loops: list[asyncio.Task[None]] = []
        self._stop_task: asyncio.Task[None] | None = None
        #: Per class; one explicit ``grace_seconds`` on stop replaces them all.
        self._grace = {cls: class_shutdown_grace(cls) for cls in classes}

    def _wake_local(self, cls: WorkClass) -> None:
        dispatcher = self.dispatchers.get(cls)
        if dispatcher is not None:
            dispatcher.wake()

    @property
    def classes(self) -> list[WorkClass]:
        return list(self.dispatchers)

    async def _arm_periodics(self) -> int:
        """Arm the periodics, waiting out a database that cannot take a connection.

        A worker that joins a rolling deploy while the database is full would
        otherwise exit at boot and crash-loop until a slot frees. Bounded: past
        ``_BOOT_DB_WAIT_SECONDS`` the error is raised as before.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _BOOT_DB_WAIT_SECONDS
        delay = 0.5
        while True:
            try:
                async with self.db.control() as session:
                    armed = await maintenance.arm_periodics(
                        session, self._registry, self.classes
                    )
                    await session.commit()
                return armed
            except (SQLAlchemyError, OSError) as exc:
                if not is_database_unavailable(exc) or loop.time() > deadline:
                    raise
                logger.warning(
                    "work.runtime.boot_waiting_for_database.degraded",
                    error_type=type(exc).__name__,
                    retry_in_seconds=delay,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    async def start(self) -> None:
        if not self.dispatchers:
            return
        armed = await self._arm_periodics()
        for cls, dispatcher in self.dispatchers.items():
            self._tasks.append(
                create_background_task(
                    dispatcher.run(), name=f"work-dispatcher-{cls.value}"
                )
            )
        self._start_loops()
        logger.info(
            "work.runtime.started",
            classes=",".join(c.value for c in self.classes),
            periodics_armed=armed,
            db_connections=self.budget.total,
        )

    def _start_loops(self) -> None:
        if self._database_url and work_settings.work_listen_enabled:
            listener = WorkWakeListener(
                self._database_url,
                {cls: d.wake for cls, d in self.dispatchers.items()},
            )
            self._loops.append(
                create_background_task(listener.run(), name="work-wake-listener")
            )
        self._loops.append(
            create_background_task(
                gauge_loop(self.db.control, work_settings.work_gauge_interval_seconds),
                name="work-gauges",
            )
        )
        self._loops.append(
            create_background_task(self._refresh_loop(), name="work-cap-refresh")
        )

    async def _refresh_loop(self) -> None:
        limits = self._limits or current_work_limit_port()
        while True:
            await asyncio.sleep(work_settings.work_cap_refresh_seconds)
            for cls in self.classes:
                try:
                    async with self.db.control() as session:
                        await maintenance.refresh_queues(session, cls, limits)
                        await session.commit()
                except SQLAlchemyError, OSError:
                    logger.warning(
                        "work.queues.refresh_failed",
                        work_class=cls.value,
                        exc_info=True,
                    )

    def begin_stop(self, grace_seconds: float | None = None) -> None:
        """Start draining now, without waiting for anyone: the SIGTERM path.

        Synchronous so a signal handler can call it from a cancelled scope:
        claiming stops at once, jobs see ``stopping`` at their next safe point,
        and the drain itself runs as a task of its own. ``stop`` awaits it.
        """
        if self._stop_task is not None or not self.dispatchers:
            return
        if grace_seconds is not None:
            self._grace = dict.fromkeys(self._grace, grace_seconds)
        self.stopping.set()
        for dispatcher in self.dispatchers.values():
            dispatcher.wake()
        logger.info(
            "work.runtime.stopping", grace_seconds=max(self._grace.values(), default=0)
        )
        self._stop_task = create_background_task(self._drain(), name="work-drain")

    async def stop(self, grace_seconds: float | None = None) -> None:
        if not self.dispatchers:
            return
        self.begin_stop(grace_seconds)
        if self._stop_task is not None:
            await asyncio.wait({self._stop_task})

    async def _drain(self) -> None:
        if self._tasks:
            await asyncio.wait(self._tasks)
        released = await asyncio.gather(
            *(d.drain(self._grace[cls]) for cls, d in self.dispatchers.items())
        )
        for task in self._loops:
            task.cancel()
        if self._loops:
            await asyncio.wait(self._loops)
        logger.info("work.runtime.stopped", released=sum(released))


def _dedicated_sessions(
    database_url: str, budget: DbBudget
) -> tuple[AsyncEngine, SessionMaker]:
    from app.core.infrastructure.db.session import create_dedicated_engine

    engine = create_dedicated_engine(
        database_url, pool_size=budget.total, logging_name="work"
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def work_runtime_lifespan(
    session_maker: SessionMaker | None = None,
    *,
    database_url: str | None,
    classes: list[WorkClass] | None = None,
    registry: WorkRegistry | None = None,
) -> AsyncIterator[WorkRuntime]:
    """Run the runtime for the duration of the block.

    Without a ``session_maker`` it opens a pool of its own on ``database_url``,
    sized to exactly what its slots may hold, and closes it on the way out.
    """
    selected = enabled_classes() if classes is None else classes
    budget = DbBudget.from_settings(selected)
    engine: AsyncEngine | None = None
    if session_maker is None:
        if database_url is None:
            raise ValueError(
                "work_runtime_lifespan needs a session_maker or a database_url"
            )
        engine, session_maker = _dedicated_sessions(database_url, budget)
    runtime = WorkRuntime(
        session_maker,
        classes=selected,
        registry=registry,
        database_url=database_url,
        budget=budget,
    )
    try:
        await runtime.start()
        yield runtime
    finally:
        await runtime.stop()
        if engine is not None:
            await engine.dispose()
