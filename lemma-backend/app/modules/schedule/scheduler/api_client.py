"""Typed API client for scheduler service."""

from __future__ import annotations

from typing import Optional
from uuid import UUID
from datetime import datetime

import aiohttp
from aiohttp import ClientError, ClientResponseError

from app.modules.schedule.scheduler.api.schemas import (
    ScheduleCronJobRequest,
    ScheduleOnceJobRequest,
    JobResponse,
    JobListResponse,
    JobStatusResponse,
)
from app.core.config import reveal_secret
from app.core.log.log import get_logger

from app.modules.schedule.domain.interfaces import SchedulerService
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.config import schedule_settings

logger = get_logger(__name__)


class SchedulerAPIClient(SchedulerService):
    """Typed API client for scheduler service.

    This client provides a convenient way to interact with the scheduler API
    from other services in the application.

    Usage:
        # Recommended: Use as context manager for multiple operations
        async with SchedulerAPIClient() as client:
            await client.schedule_cron_job(...)
            await client.list_jobs()

        # For single operations, session is managed automatically
        client = SchedulerAPIClient()
        await client.schedule_cron_job(...)  # Session created and closed per request
    """

    async def schedule_job(self, schedule: ScheduleEntity) -> None:
        """Schedule a job for the schedule."""
        if schedule.schedule_type == ScheduleType.TIME:
            config = schedule.time_config
            if not config:
                logger.warning(f"Schedule {schedule.id} missing time config")
                return

            payload = dict(schedule.config.get("payload") or {})
            payload.setdefault("schedule_id", str(schedule.id))

            if config.scheduled_at:
                await self.schedule_once_job(
                    schedule_id=schedule.id,
                    run_date=datetime.fromisoformat(config.scheduled_at),
                    payload=payload,
                    replace_existing=True,
                )
            elif config.cron:
                await self.schedule_cron_job(
                    schedule_id=schedule.id,
                    cron_expression=config.cron,
                    payload=payload,
                    replace_existing=True,
                )
            else:
                logger.warning(
                    f"Schedule {schedule.id} check failed: no cron or scheduled_at"
                )
        else:
            logger.warning(
                f"Unsupported schedule type for scheduling: {schedule.schedule_type}"
            )

    async def remove_job(self, schedule_id: UUID) -> None:
        """Remove a scheduled job."""
        try:
            await self._request(
                method="DELETE",
                path=f"/scheduler/jobs/{schedule_id}",
            )
        except ClientResponseError as e:
            if e.status == 404:
                logger.warning(f"Job for schedule {schedule_id} not found to delete")
            else:
                raise

    def __init__(self, base_url: Optional[str] = None):
        """Initialize the scheduler API client.

        Args:
            base_url: Base URL of the scheduler API. Defaults to scheduler service URL.
        """
        # Default to scheduler service URL (typically http://localhost:8001)
        self.base_url = (base_url or schedule_settings.scheduler_api_url).rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._managed_session = (
            False  # Track if we're managing the session via context manager
        )

    async def __aenter__(self):
        """Async context manager entry."""
        self._session = aiohttp.ClientSession()
        self._managed_session = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._session:
            await self._session.close()
            self._session = None
            self._managed_session = False

    def __del__(self):
        """Cleanup on deletion - warn if session wasn't properly closed."""
        if self._session is not None and not self._session.closed:
            logger.warning(
                "SchedulerAPIClient session was not properly closed. "
                "Consider using the client as a context manager: "
                "'async with SchedulerAPIClient() as client: ...'"
            )

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the scheduler API.

        Args:
            method: HTTP method (GET, POST, DELETE, PATCH)
            path: API path (e.g., '/scheduler/jobs/cron')
            json_data: Optional JSON data for request body

        Returns:
            Response JSON data

        Raises:
            ClientError: If the request fails
        """
        url = f"{self.base_url}{path}"

        # If using context manager, use the persistent session
        if self._managed_session and self._session:
            return await self._make_request(self._session, method, url, json_data)

        # Otherwise, create a temporary session for this request
        async with aiohttp.ClientSession() as session:
            return await self._make_request(session, method, url, json_data)

    async def _make_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        json_data: Optional[dict] = None,
    ) -> dict:
        """Execute the actual HTTP request.

        Args:
            session: The aiohttp session to use
            method: HTTP method
            url: Full URL
            json_data: Optional JSON data

        Returns:
            Response JSON data
        """
        headers: dict[str, str] = {}
        token = reveal_secret(schedule_settings.scheduler_internal_token)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with session.request(
                method=method,
                url=url,
                json=json_data,
                headers=headers or None,
            ) as response:
                response.raise_for_status()

                # Handle 204 No Content
                if response.status == 204:
                    return {}

                return await response.json()
        except ClientResponseError as e:
            logger.error(f"Scheduler API error: {e.status} - {e.message}")
            raise
        except ClientError as e:
            logger.error(f"Scheduler API request failed: {e}")
            raise

    async def schedule_cron_job(
        self,
        schedule_id: UUID,
        cron_expression: str,
        payload: Optional[dict] = None,
        replace_existing: bool = True,
    ) -> JobResponse:
        """Schedule a cron-based job.

        Args:
            schedule_id: The schedule ID (also used as job_id)
            cron_expression: Cron expression (e.g., "*/5 * * * *")
            payload: Optional payload to include in the event
            replace_existing: Replace job if it already exists

        Returns:
            JobResponse with job details
        """
        request = ScheduleCronJobRequest(
            schedule_id=schedule_id,
            cron_expression=cron_expression,
            payload=payload,
            replace_existing=replace_existing,
        )

        data = await self._request(
            method="POST",
            path="/scheduler/jobs/cron",
            json_data=request.model_dump(mode="json"),
        )

        return JobResponse.model_validate(data)

    async def schedule_once_job(
        self,
        schedule_id: UUID,
        run_date: datetime,
        payload: Optional[dict] = None,
        replace_existing: bool = True,
    ) -> JobResponse:
        """Schedule a one-time job.

        Args:
            schedule_id: The schedule ID (also used as job_id)
            run_date: Datetime when to run the job
            payload: Optional payload to include in the event
            replace_existing: Replace job if it already exists

        Returns:
            JobResponse with job details
        """
        request = ScheduleOnceJobRequest(
            schedule_id=schedule_id,
            run_date=run_date,
            payload=payload,
            replace_existing=replace_existing,
        )

        data = await self._request(
            method="POST",
            path="/scheduler/jobs/once",
            json_data=request.model_dump(mode="json"),
        )

        return JobResponse.model_validate(data)

    async def list_jobs(self) -> JobListResponse:
        """List all scheduled jobs.

        Returns:
            JobListResponse with list of all jobs
        """
        data = await self._request(
            method="GET",
            path="/scheduler/jobs",
        )

        return JobListResponse.model_validate(data)

    async def get_job_status(self, schedule_id: UUID) -> JobStatusResponse:
        """Get the status of a specific job.

        Args:
            schedule_id: The schedule ID (also used as job_id)

        Returns:
            JobStatusResponse with job status
        """
        data = await self._request(
            method="GET",
            path=f"/scheduler/jobs/{schedule_id}",
        )

        return JobStatusResponse.model_validate(data)

    async def pause_job(self, schedule_id: UUID) -> JobStatusResponse:
        """Pause a scheduled job.

        Args:
            schedule_id: The schedule ID (also used as job_id)

        Returns:
            JobStatusResponse with updated job status
        """
        data = await self._request(
            method="PATCH",
            path=f"/scheduler/jobs/{schedule_id}/pause",
        )

        return JobStatusResponse.model_validate(data)

    async def resume_job(self, schedule_id: UUID) -> JobStatusResponse:
        """Resume a paused job.

        Args:
            schedule_id: The schedule ID (also used as job_id)

        Returns:
            JobStatusResponse with updated job status
        """
        data = await self._request(
            method="PATCH",
            path=f"/scheduler/jobs/{schedule_id}/resume",
        )

        return JobStatusResponse.model_validate(data)

    async def close(self):
        """Close the HTTP session.

        Note: Only needed if you created a session without using context manager.
        If using context manager, this is called automatically on exit.
        """
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            self._managed_session = False


# Convenience function to get a client instance
def get_scheduler_client(base_url: Optional[str] = None) -> SchedulerAPIClient:
    """Get a scheduler API client instance.

    Args:
        base_url: Optional base URL for the scheduler API

    Returns:
        SchedulerAPIClient instance
    """
    return SchedulerAPIClient(base_url=base_url)
