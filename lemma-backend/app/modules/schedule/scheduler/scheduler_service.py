"""Scheduler service using APScheduler with SQLAlchemy job store.

This service manages scheduled jobs and emits events via FastStream when jobs fire.
"""

from __future__ import annotations

from typing import Optional
from datetime import datetime
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.events import EVENT_JOB_ERROR
from pytz import utc
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.schedule.scheduler.events import get_event_emitter
from app.modules.schedule.scheduler.executor import (
    ScheduledTimeAsyncIOExecutor,
    current_scheduled_run_time,
)

logger = get_logger(__name__)


def build_sync_jobstore_url(database_url: str) -> str:
    """Build the synchronous psycopg URL used by APScheduler.

    The application uses asyncpg, whose TLS query parameter is ``ssl``. Psycopg
    expects the libpq spelling, ``sslmode``. Keeping that parameter unchanged
    makes Azure PostgreSQL reject the connection before the scheduler starts.
    """
    url = make_url(database_url)

    if url.drivername in {"postgresql", "postgresql+asyncpg"}:
        url = url.set(drivername="postgresql+psycopg")

    query = dict(url.query)
    if "ssl" in query and "sslmode" not in query:
        query["sslmode"] = query.pop("ssl")
        url = url.set(query=query)

    return url.render_as_string(hide_password=False)


async def execute_scheduled_job(schedule_id: str, payload: dict | None = None):
    """Static function to execute scheduled jobs.

    This function is called by APScheduler when a job fires.
    It must be a module-level function to be serializable.

    Args:
        schedule_id: The schedule ID as a string (will be converted to UUID)
        payload: Optional payload data
    """
    from uuid import UUID

    emitter = get_event_emitter()

    schedule_uuid = UUID(schedule_id)
    await emitter.emit_scheduled_job_event(
        schedule_id=schedule_uuid,
        payload=payload or {},
        scheduled_at=current_scheduled_run_time(),
    )


class SchedulerService:
    """Manages APScheduler for time-based schedules.

    When jobs fire, events are emitted to FastStream instead of executing directly.
    """

    def __init__(self):
        # Convert async database URL to sync for APScheduler
        # APScheduler's SQLAlchemyJobStore requires a synchronous engine
        sync_db_url = build_sync_jobstore_url(str(settings.database_url))

        # Configure job stores - using PostgreSQL with synchronous engine
        jobstores = {"default": SQLAlchemyJobStore(url=sync_db_url)}

        # Configure executors
        executors = {"default": ScheduledTimeAsyncIOExecutor()}

        # Job defaults
        job_defaults = {
            "coalesce": True,  # Combine missed executions
            "max_instances": 3,  # Max concurrent instances
            "misfire_grace_time": 300,  # 5 minutes grace period
        }

        # Create scheduler
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=utc,
        )

        self._started = False

    async def start(self):
        """Start the scheduler and event emitter."""
        if not self._started:
            # Start event emitter first
            emitter = get_event_emitter()
            await emitter.start()

            # Start scheduler
            self.scheduler.start()
            self._started = True
            # Stable terminal-failure event for dashboards/alerts. APScheduler
            # does not expose job duration in its events, so only error_type is
            # emitted. There is no scheduler-level "cycle error" event in this
            # version, so per-job errors are the failure signal.
            self.scheduler.add_listener(self._on_scheduler_event, EVENT_JOB_ERROR)

    def _on_scheduler_event(self, event) -> None:
        """Emit one stable failure event per APScheduler job error."""
        exception = getattr(event, "exception", None)
        error_type = type(exception).__name__ if exception else "UnknownError"
        logger.error(
            "scheduler.job.failed",
            job_id=getattr(event, "job_id", None),
            error_type=error_type,
        )

    async def shutdown(self, wait: bool = True):
        """Shutdown the scheduler and event emitter."""
        if self._started:
            self.scheduler.shutdown(wait=wait)
            self._started = False

            # Stop event emitter
            emitter = get_event_emitter()
            await emitter.stop()

            logger.debug("schedule.scheduler_service.apscheduler_shutdown.observed")

    def add_cron_job(
        self,
        schedule_id: UUID,
        cron_expression: str,
        payload: Optional[dict] = None,
        replace_existing: bool = True,
    ) -> None:
        """Add a cron-based job.

        Args:
            schedule_id: The schedule ID (also used as job_id)
            cron_expression: Cron expression (e.g., "*/5 * * * *")
            payload: Optional payload to include in the event
            replace_existing: Replace if job exists
        """
        # Parse cron expression
        parts = cron_expression.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expression}")

        minute, hour, day, month, day_of_week = parts

        apscheduler_trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=utc,
        )

        # Use schedule_id as job_id
        job_id = str(schedule_id)

        # Use string reference to the static function for serialization
        self.scheduler.add_job(
            func="app.modules.schedule.scheduler.scheduler_service:execute_scheduled_job",
            trigger=apscheduler_trigger,
            id=job_id,
            kwargs={"schedule_id": job_id, "payload": payload},
            replace_existing=replace_existing,
        )

        logger.debug(
            "schedule.scheduler_service.added_cron_job_schedule_schedule.observed",
            job_id=job_id,
            schedule_id=schedule_id,
        )

    def add_once_job(
        self,
        schedule_id: UUID,
        run_date: datetime,
        payload: Optional[dict] = None,
        replace_existing: bool = True,
    ) -> None:
        """Add a one-time scheduled job.

        Args:
            schedule_id: The schedule ID (also used as job_id)
            run_date: Datetime when to run the job (timezone-aware)
            payload: Optional payload to include in the event
            replace_existing: Replace if job exists
        """
        # Ensure run_date is timezone-aware
        if run_date.tzinfo is None:
            run_date = utc.localize(run_date)
        else:
            run_date = run_date.astimezone(utc)

        apscheduler_trigger = DateTrigger(run_date=run_date, timezone=utc)

        # Use schedule_id as job_id
        job_id = str(schedule_id)

        # Use string reference to the static function for serialization
        self.scheduler.add_job(
            func="app.modules.schedule.scheduler.scheduler_service:execute_scheduled_job",
            trigger=apscheduler_trigger,
            id=job_id,
            kwargs={"schedule_id": job_id, "payload": payload},
            replace_existing=replace_existing,
        )

        logger.debug(
            "schedule.scheduler_service.added_one_time_job_schedule.observed",
            job_id=job_id,
            schedule_id=schedule_id,
        )

    def remove_job(self, job_id: str) -> None:
        """Remove a job by ID."""
        try:
            self.scheduler.remove_job(job_id)
            logger.debug(
                "schedule.scheduler_service.removed_job.observed", job_id=job_id
            )
        except Exception:
            logger.debug(
                'schedule.scheduler_service.remove_job.diagnostic', job_id=job_id
            )

    def pause_job(self, job_id: str) -> None:
        """Pause a job."""
        try:
            self.scheduler.pause_job(job_id)
            logger.debug(
                "schedule.scheduler_service.paused_job.observed", job_id=job_id
            )
        except Exception:
            logger.debug(
                'schedule.scheduler_service.pause_job.diagnostic', job_id=job_id
            )

    def resume_job(self, job_id: str) -> None:
        """Resume a job."""
        try:
            self.scheduler.resume_job(job_id)
            logger.debug(
                "schedule.scheduler_service.resumed_job.observed", job_id=job_id
            )
        except Exception:
            logger.debug(
                'schedule.scheduler_service.resume_job.diagnostic', job_id=job_id
            )

    def get_job(self, job_id: str):
        """Get job by ID."""
        return self.scheduler.get_job(job_id)

    def get_jobs(self, jobstore: str = "default"):
        """Get all jobs."""
        return self.scheduler.get_jobs(jobstore=jobstore)


# Global scheduler instance
_scheduler_service: Optional[SchedulerService] = None


def get_scheduler_service() -> SchedulerService:
    """Get the global scheduler service instance."""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service
