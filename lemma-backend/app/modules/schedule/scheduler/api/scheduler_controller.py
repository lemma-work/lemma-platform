"""API controller for scheduler operations."""

from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import Response

from app.modules.schedule.scheduler.scheduler_service import get_scheduler_service
from app.modules.schedule.scheduler.api.schemas import (
    ScheduleCronJobRequest,
    ScheduleOnceJobRequest,
    JobResponse,
    JobListResponse,
    JobStatusResponse,
)
from app.core.config import reveal_secret
from app.core.log.log import get_logger
from app.modules.schedule.config import schedule_settings

logger = get_logger(__name__)


async def require_scheduler_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Service-to-service auth for the scheduler sidecar's job API.

    The sidecar is an internal singleton reached only by the backend's
    ``SchedulerAPIClient``. When ``scheduler_internal_token`` is configured the
    caller must present it as a bearer token; when it is unset (e.g. local
    single-process dev) the check is skipped so nothing breaks out of the box.
    """
    expected = reveal_secret(schedule_settings.scheduler_internal_token)
    if not expected:
        return

    provided: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:]

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing scheduler token",
        )


router = APIRouter(
    prefix="/scheduler/jobs",
    tags=["Scheduler"],
    dependencies=[Depends(require_scheduler_token)],
)


@router.post(
    "/cron",
    operation_id="scheduler.job.schedule_cron",
    summary="Schedule Cron Job",
    description="Schedule a recurring job using a cron expression",
    status_code=status.HTTP_201_CREATED,
    response_model=JobResponse,
)
async def schedule_cron_job(
    data: ScheduleCronJobRequest,
) -> JobResponse:
    """Schedule a cron-based job."""
    scheduler = get_scheduler_service()

    try:
        scheduler.add_cron_job(
            schedule_id=data.schedule_id,
            cron_expression=data.cron_expression,
            payload=data.payload,
            replace_existing=data.replace_existing,
        )

        # Use schedule_id as job_id
        job_id = str(data.schedule_id)

        # Get the job to return next run time
        job = scheduler.get_job(job_id)
        next_run_time = job.next_run_time if job else None

        return JobResponse(
            job_id=job_id,
            schedule_id=data.schedule_id,
            next_run_time=next_run_time,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid cron expression: {e}",
        )
    except Exception as e:
        logger.error(f"Failed to schedule cron job: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to schedule cron job",
        )


@router.post(
    "/once",
    operation_id="scheduler.job.schedule_once",
    summary="Schedule One-Time Job",
    description="Schedule a job to run once at a specific time",
    status_code=status.HTTP_201_CREATED,
    response_model=JobResponse,
)
async def schedule_once_job(
    data: ScheduleOnceJobRequest,
) -> JobResponse:
    """Schedule a one-time job."""
    scheduler = get_scheduler_service()

    try:
        scheduler.add_once_job(
            schedule_id=data.schedule_id,
            run_date=data.run_date,
            payload=data.payload,
            replace_existing=data.replace_existing,
        )

        # Use schedule_id as job_id
        job_id = str(data.schedule_id)

        # Get the job to return next run time
        job = scheduler.get_job(job_id)
        next_run_time = job.next_run_time if job else None

        return JobResponse(
            job_id=job_id,
            schedule_id=data.schedule_id,
            next_run_time=next_run_time,
        )
    except Exception as e:
        logger.error(f"Failed to schedule one-time job: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to schedule one-time job",
        )


@router.get(
    "",
    operation_id="scheduler.job.list",
    summary="List All Jobs",
    description="Get a list of all scheduled jobs",
    status_code=status.HTTP_200_OK,
    response_model=JobListResponse,
)
async def list_jobs() -> JobListResponse:
    """List all scheduled jobs."""
    scheduler = get_scheduler_service()
    jobs = scheduler.get_jobs()

    job_responses = []
    for job in jobs:
        # job_id is the same as schedule_id (as string)
        try:
            schedule_id = UUID(job.id)
        except ValueError:
            # Fallback if job_id is not a valid UUID
            schedule_id = UUID("00000000-0000-0000-0000-000000000000")

        job_responses.append(
            JobResponse(
                job_id=job.id,
                schedule_id=schedule_id,
                next_run_time=job.next_run_time,
                job_state=job.state.name if hasattr(job, "state") else None,
            )
        )

    return JobListResponse(jobs=job_responses, total=len(job_responses))


@router.get(
    "/{schedule_id}",
    operation_id="scheduler.job.get",
    summary="Get Job Status",
    description="Get the status of a specific job by schedule_id",
    status_code=status.HTTP_200_OK,
    response_model=JobStatusResponse,
)
async def get_job_status(
    schedule_id: UUID,
) -> JobStatusResponse:
    """Get the status of a specific job."""
    scheduler = get_scheduler_service()
    job_id = str(schedule_id)
    job = scheduler.get_job(job_id)

    if not job:
        return JobStatusResponse(
            job_id=job_id,
            exists=False,
        )

    return JobStatusResponse(
        job_id=job_id,
        exists=True,
        next_run_time=job.next_run_time,
        job_state=job.state.name if hasattr(job, "state") else None,
    )


@router.delete(
    "/{schedule_id}",
    operation_id="scheduler.job.delete",
    summary="Remove Job",
    description="Remove a scheduled job by schedule_id",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_job(
    schedule_id: UUID,
) -> Response:
    """Remove a scheduled job."""
    scheduler = get_scheduler_service()
    job_id = str(schedule_id)
    scheduler.remove_job(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{schedule_id}/pause",
    operation_id="scheduler.job.pause",
    summary="Pause Job",
    description="Pause a scheduled job by schedule_id",
    status_code=status.HTTP_200_OK,
    response_model=JobStatusResponse,
)
async def pause_job(
    schedule_id: UUID,
) -> JobStatusResponse:
    """Pause a scheduled job."""
    scheduler = get_scheduler_service()
    job_id = str(schedule_id)
    job = scheduler.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job for schedule {schedule_id} not found",
        )

    scheduler.pause_job(job_id)

    # Get updated job status
    job = scheduler.get_job(job_id)
    return JobStatusResponse(
        job_id=job_id,
        exists=True,
        next_run_time=job.next_run_time if job else None,
        job_state=job.state.name if job and hasattr(job, "state") else None,
    )


@router.patch(
    "/{schedule_id}/resume",
    operation_id="scheduler.job.resume",
    summary="Resume Job",
    description="Resume a paused job by schedule_id",
    status_code=status.HTTP_200_OK,
    response_model=JobStatusResponse,
)
async def resume_job(
    schedule_id: UUID,
) -> JobStatusResponse:
    """Resume a paused job."""
    scheduler = get_scheduler_service()
    job_id = str(schedule_id)
    job = scheduler.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job for schedule {schedule_id} not found",
        )

    scheduler.resume_job(job_id)

    # Get updated job status
    job = scheduler.get_job(job_id)
    return JobStatusResponse(
        job_id=job_id,
        exists=True,
        next_run_time=job.next_run_time if job else None,
        job_state=job.state.name if job and hasattr(job, "state") else None,
    )
