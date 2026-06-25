"""Background handlers and event projections for function job execution."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter

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
from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext, streaq_task, streaq_worker
from app.modules.function.domain.entities import FunctionRunStatus
from app.modules.function.domain.events import (
    FUNCTION_RUN_EVENTS_STREAM,
    FunctionRunCompletedEvent,
    FunctionRunExecutionRequestedEvent,
    FunctionRunFailedEvent,
    FunctionRunLogsUpdatedEvent,
    FunctionRunStartedEvent,
)
from app.modules.function.domain.errors import (
    FunctionNotFoundError,
    FunctionRunNotFoundError,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.function.services.function_service import _JOB_FUNCTION_TIMEOUT_SECONDS
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()


def provide_job_queue() -> SharedStreaqJobQueue:
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@router.subscriber(
    stream=redis_stream_sub(
        FUNCTION_RUN_EVENTS_STREAM,
        group="function-run-events",
        consumer="function-run-events-consumer",
    )
)
async def handle_function_run_event(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
) -> None:
    """Project function run lifecycle events into persistence and jobs."""
    event_type = event.get("event_type")

    if event_type == FunctionRunExecutionRequestedEvent.get_event_type():
        parsed = FunctionRunExecutionRequestedEvent.model_validate(event)
        fs_logger.info("Enqueuing function run execution for %s", parsed.run_id)
        job = await job_queue.enqueue(
            "process_function_run",
            run_id=str(parsed.run_id),
            _job_id=f"function:{parsed.run_id}",
        )
        job_id = (
            getattr(job, "job_id", None)
            or getattr(job, "_job_id", None)
            or getattr(job, "id", None)
        )
        if job_id is not None:
            async with uow_factory() as uow:
                await FunctionRunRepository(uow).update_run(
                    parsed.run_id, job_id=str(job_id)
                )
        return

    if event_type == FunctionRunStartedEvent.get_event_type():
        parsed = FunctionRunStartedEvent.model_validate(event)
        async with uow_factory() as uow:
            await FunctionRunRepository(uow).update_run(
                parsed.run_id,
                status=FunctionRunStatus.RUNNING,
                started_at=parsed.started_at,
                user_email=parsed.user_email,
                workspace_session_id=parsed.workspace_session_id,
                workspace_process_id=parsed.workspace_process_id,
            )
        return

    if event_type == FunctionRunLogsUpdatedEvent.get_event_type():
        parsed = FunctionRunLogsUpdatedEvent.model_validate(event)
        async with uow_factory() as uow:
            await FunctionRunRepository(uow).update_run(
                parsed.run_id, logs=parsed.logs
            )
        return

    if event_type == FunctionRunCompletedEvent.get_event_type():
        parsed = FunctionRunCompletedEvent.model_validate(event)
        async with uow_factory() as uow:
            await FunctionRunRepository(uow).update_run(
                parsed.run_id,
                status=FunctionRunStatus.COMPLETED,
                output_data=parsed.output_data,
                error=None,
                logs=parsed.logs,
                completed_at=parsed.completed_at,
                workspace_session_id=parsed.workspace_session_id,
                workspace_process_id=parsed.workspace_process_id,
            )
        return

    if event_type == FunctionRunFailedEvent.get_event_type():
        parsed = FunctionRunFailedEvent.model_validate(event)
        async with uow_factory() as uow:
            await FunctionRunRepository(uow).update_run(
                parsed.run_id,
                status=FunctionRunStatus.FAILED,
                error=parsed.error,
                logs=parsed.logs,
                completed_at=parsed.completed_at,
                workspace_session_id=parsed.workspace_session_id,
                workspace_process_id=parsed.workspace_process_id,
            )


@streaq_task(name="process_function_run")
async def process_function_run(
    run_id: str,
) -> None:
    """Execute one queued function run without holding a DB session open."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    task_ctx = process_function_run.context
    parsed_run_id = UUID(run_id)
    logger.info(
        "Starting process_function_run job %s for run %s",
        task_ctx.task_id,
        run_id,
    )

    last_error: Exception | None = None
    function_id: UUID | None = None
    for attempt in range(10):
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

            service = worker_ctx.build_function_service_with_factory()
            await service.execute_run_by_id(
                parsed_run_id,
                timeout_seconds=_JOB_FUNCTION_TIMEOUT_SECONDS,
            )
            logger.info(
                "Finished process_function_run job %s for run %s",
                task_ctx.task_id,
                run_id,
            )
            return
        except Exception as exc:
            last_error = exc
            if "not found" not in str(exc).lower() or attempt == 9:
                logger.exception("Function run job failed for run %s", run_id)
                if function_id is None:
                    raise
                # The service-level terminal persist (_persist_terminal_run)
                # already emits a completion/failure event when the run reaches a
                # committed terminal state. Only publish here as a fallback when
                # the failure escaped before that commit (e.g. run vanished, or a
                # commit error), so we don't double-publish FunctionRunFailedEvent.
                already_terminal = False
                try:
                    async with worker_ctx.uow() as uow:
                        existing = await FunctionRunRepository(uow).get_run(
                            parsed_run_id
                        )
                        already_terminal = (
                            existing is not None
                            and existing.status
                            in {
                                FunctionRunStatus.COMPLETED,
                                FunctionRunStatus.FAILED,
                            }
                            and existing.completed_at is not None
                        )
                except Exception:
                    already_terminal = False
                if not already_terminal:
                    await EventPublisher.publish(
                        FunctionRunFailedEvent.stream_name(),
                        FunctionRunFailedEvent(
                            run_id=parsed_run_id,
                            function_id=function_id,
                            error=str(exc),
                            logs=None,
                            completed_at=datetime.now(),
                        ),
                    )
                return
            await asyncio.sleep(0.2)

    if last_error is not None:
        raise last_error
