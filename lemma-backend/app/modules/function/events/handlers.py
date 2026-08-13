"""Background jobs for asynchronous function execution and reconciliation."""

from __future__ import annotations

import time
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    Lane,
    streaq_cron,
    streaq_task,
    streaq_worker,
)
from app.core.config import settings
from app.modules.function.domain.errors import (
    FunctionNotFoundError,
    FunctionRunNotFoundError,
    FunctionRunQueueUnavailable,
)
from app.modules.function.infrastructure.function_run_queue import (
    StreaqFunctionRunQueue,
)
from app.modules.function.application.runtime_policy import (
    FUNCTION_JOB_CALLBACK_GRACE_SECONDS,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(get_session_maker())


async def _fail_run_after_worker_error(
    worker_ctx: AppWorkerContext,
    run_id: UUID,
    error: Exception,
) -> None:
    """Persist the fallback terminal state without replaying the invocation."""

    async with worker_ctx.uow() as uow:
        await FunctionExecutionRepository(uow).fail_unfinished(
            run_id,
            error=f"Function execution failed ({type(error).__name__})",
        )


@streaq_task(name="process_function_run")
async def process_function_run(
    run_id: str,
) -> None:
    """Execute one queued function run without holding a DB session open."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    parsed_run_id = UUID(run_id)

    function_id: UUID | None = None
    try:
        async with worker_ctx.uow() as uow:
            service = worker_ctx.build_function_service(uow)
            run = await service.run_repository.get_run(parsed_run_id)
            if run is None:
                raise FunctionRunNotFoundError(f"Run {parsed_run_id} not found")
            function_id = run.function_id

            function = await service.repository.get(run.function_id)
            if function is None:
                raise FunctionNotFoundError(f"Function {run.function_id} not found")

        use_cases = worker_ctx.build_function_use_cases()
        await use_cases.execute_run_by_id(parsed_run_id)
    except Exception as exc:
        logger.debug(
            "function.handlers.function_run_job.propagated",
            run_id=run_id,
            exc_info=True,
        )
        if function_id is None:
            raise
        # The dispatcher normally persists the terminal failure itself. Guard
        # this fallback with the same row-state transition so it cannot overwrite
        # a terminal callback that committed concurrently. This task never loops
        # back into dispatch after an invocation may have been accepted.
        await _fail_run_after_worker_error(worker_ctx, parsed_run_id, exc)


async def _reconcile_unqueued_function_runs(
    *,
    uow_factory: UnitOfWorkFactory,
    now: datetime,
    limit: int = 100,
) -> int:
    """Republish committed asynchronous PENDING runs without holding a connection."""

    async with uow_factory() as uow:
        run_ids = await FunctionRunRepository(uow).list_pending_async_runs(
            now=now,
            limit=limit,
        )

    queue = StreaqFunctionRunQueue(get_streaq_job_queue())
    published = 0
    for run_id in run_ids:
        try:
            await queue.enqueue(run_id)
            published += 1
        except FunctionRunQueueUnavailable as exc:
            logger.warning(
                "function.handlers.run_reconcile_enqueue_failed.degraded",
                run_id=str(run_id),
                error_type=type(exc).__name__,
            )
            continue
    return published


async def _guard_cron(task_name: str, body: Coroutine[Any, Any, None]) -> None:
    """Keep one failed tick from taking the schedule down with it.

    A cron that raises stops contributing until the next tick anyway, so the
    only thing an escaping exception buys is a less useful log line. Both crons
    in this module share this boundary so the module keeps exactly one broad
    catch rather than one per schedule.
    """

    try:
        await body
    except Exception:
        logger.error(
            "function.handlers.cron.failed",
            task_name=task_name,
            exc_info=True,
        )


@streaq_cron("17 * * * *", name="prune_function_runs")
async def prune_function_runs() -> None:
    """Reclaim finished runs past the retention window.

    Nothing removed a function run before this existed, and a run carries its
    input, its output and its captured logs -- so the table grew in bytes
    faster than in rows, and every query over it got slower for the life of the
    install.

    Batched and budgeted for the same reason as the event-delivery sweep: the
    first run against an install that has never pruned has a large backlog to
    work through, and it has to do that without monopolising the database or
    running into the next tick. Stopping early costs nothing -- the cutoff is
    recomputed on the next run and the remainder is still eligible.

    Offset off the hour so it does not start alongside the event-delivery
    sweep; both are delete-heavy on the same database.
    """

    await _guard_cron("prune_function_runs", _prune_function_runs())


async def _prune_function_runs() -> None:
    from app.core.config import settings

    budget = settings.function_run_retention_budget_seconds
    if budget <= 0:
        return

    batch_size = settings.function_run_retention_batch_size
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.function_run_retention_days
    )
    uow_factory = provide_uow_factory()
    started = time.monotonic()
    removed = 0
    while True:
        async with uow_factory() as uow:
            batch = await FunctionRunRepository(uow).delete_terminal_before(
                cutoff=cutoff,
                batch_size=batch_size,
            )
        removed += batch
        if batch < batch_size or (time.monotonic() - started) >= budget:
            break
    if removed:
        logger.debug(
            "function.handlers.prune_function_runs.observed",
            deleted_count=removed,
        )


@streaq_cron("* * * * *", name="reconcile_function_runs")
async def reconcile_function_runs() -> None:
    """Recover unqueued runs and fail executions past their immutable deadline."""

    await _guard_cron("reconcile_function_runs", _reconcile_function_runs())


async def _reconcile_function_runs() -> None:
    now = datetime.now(timezone.utc)
    uow_factory = provide_uow_factory()
    await _reconcile_unqueued_function_runs(
        uow_factory=uow_factory,
        now=now,
    )
    async with uow_factory() as uow:
        await FunctionRunRepository(uow).fail_expired(
            now=now,
            job_callback_grace_seconds=FUNCTION_JOB_CALLBACK_GRACE_SECONDS,
        )


@streaq_cron(
    settings.function_revision_retention_cron,
    name="sweep_function_revisions",
    lane=Lane.BULK,
)
async def sweep_function_revisions() -> None:
    """Daily backstop for revisions whose retention window has passed.

    Pruning already runs inline on a code save, which is when a function's
    storage grows by an artifact. This catches what that structurally cannot: a
    function that stops being edited, whose surplus revisions would otherwise
    age forever with nothing re-evaluating them.

    Bulk lane: it is slow, bursty and touches object storage.
    """
    if not settings.function_revision_retention_enabled:
        return
    try:
        pruned = await _sweep_function_revisions(
            provide_uow_factory(),
            batch_size=settings.function_revision_retention_batch,
        )
        if pruned:
            logger.info(
                "function.handlers.sweep_function_revisions.observed",
                pruned_function_count=pruned,
            )
    except Exception:
        # Swallowed at the cron boundary so one bad tick does not stop the next.
        logger.error("function.handlers.sweep_function_revisions.failed", exc_info=True)


async def _sweep_function_revisions(
    uow_factory: UnitOfWorkFactory, *, batch_size: int
) -> int:
    """Prune one bounded batch of functions. Extracted from the cron so it can be
    driven directly in tests with a fake factory."""
    from sqlalchemy import select

    from app.modules.function.api.dependencies import build_function_service
    from app.modules.function.infrastructure.models import FunctionModel
    from app.modules.function.services.function_revision_retention import (
        FunctionRevisionRetention,
    )

    async with uow_factory() as uow:
        function_ids = list(
            (
                await uow.session.execute(
                    select(FunctionModel.id)
                    .order_by(FunctionModel.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

    pruned_functions = 0
    for function_id in function_ids:
        # One short unit of work per function, then its storage deletes outside
        # it -- no connection is held across object deletion, and one bad
        # function cannot abort the sweep.
        try:
            async with uow_factory() as uow:
                service = build_function_service(uow)
                function = await service.repository.get(function_id)
                if function is None:
                    continue
                retention = FunctionRevisionRetention(
                    service.repository, service.storage_factory
                )
                plan = await retention.plan(function)
                await uow.commit()
            if plan.is_empty:
                continue
            await retention.execute(plan)
            pruned_functions += 1
        except Exception:
            logger.warning(
                "function.handlers.sweep_function_revisions.skipped",
                function_id=str(function_id),
                exc_info=True,
            )
    return pruned_functions
