"""Background jobs for asynchronous function execution and reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
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
    streaq_cron,
    streaq_task,
    streaq_worker,
)
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


@streaq_cron("* * * * *", name="reconcile_function_runs")
async def reconcile_function_runs() -> None:
    """Recover unqueued runs and fail executions past their immutable deadline."""

    try:
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
    except Exception:
        logger.error(
            "function.handlers.reconcile_function_runs.failed",
            exc_info=True,
        )
