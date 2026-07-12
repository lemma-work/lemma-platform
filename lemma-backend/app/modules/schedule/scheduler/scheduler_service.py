"""Scheduler service using APScheduler with SQLAlchemy job store.

This service manages scheduled jobs and emits events via FastStream when jobs fire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from pytz import utc
from sqlalchemy import select

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.log.log import get_logger
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.scheduler.events import get_event_emitter
from app.modules.schedule.scheduler.executor import (
    ScheduledTimeAsyncIOExecutor,
    current_scheduled_run_time,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class TimeScheduleJob:
    id: UUID
    user_id: UUID
    config: dict


async def load_active_time_schedules() -> list[TimeScheduleJob]:
    table = Schedule.__table__
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(table.c.id, table.c.user_id, table.c.config).where(
                    table.c.schedule_type == ScheduleType.TIME,
                    table.c.is_active.is_(True),
                )
            )
        ).mappings()
        return [
            TimeScheduleJob(
                id=row["id"],
                user_id=row["user_id"],
                config=dict(row["config"] or {}),
            )
            for row in rows
        ]


async def execute_scheduled_job(
    schedule_id: str,
    user_id: str,
    payload: dict | None = None,
):
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
        user_id=UUID(user_id),
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
        sync_db_url = str(settings.database_url)
        # Replace asyncpg with psycopg2 for synchronous connection
        if "+asyncpg" in sync_db_url:
            sync_db_url = sync_db_url.replace("+asyncpg", "+psycopg")
        elif "postgresql+asyncpg" in sync_db_url:
            sync_db_url = sync_db_url.replace(
                "postgresql+asyncpg", "postgresql+psycopg"
            )
        # If it's already sync or doesn't have a driver, ensure it has psycopg2
        elif sync_db_url.startswith("postgresql://") and "+" not in sync_db_url:
            sync_db_url = sync_db_url.replace("postgresql://", "postgresql+psycopg://")

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
            emitter = get_event_emitter()
            await emitter.start()
            try:
                self.scheduler.start(paused=True)
                await self.reconcile_time_schedule_jobs()
                self.scheduler.resume()
            except Exception:
                if self.scheduler.running:
                    self.scheduler.shutdown(wait=False)
                await emitter.stop()
                raise
            self._started = True
            logger.info("APScheduler started with reconciled time jobs")

    async def reconcile_time_schedule_jobs(self) -> None:
        """Replace logical TIME jobs from authoritative schedule rows.

        Workflow wait timers are not logical schedule rows and carry a
        ``workflow_run_id`` payload instead of ``payload.schedule_id``; they are
        deliberately left untouched.
        """
        schedules = await load_active_time_schedules()

        now = datetime.now(timezone.utc)
        desired: list[
            tuple[TimeScheduleJob, dict, datetime | None, str | None]
        ] = []
        desired_ids: set[str] = set()
        for schedule in schedules:
            config = dict(schedule.config or {})
            payload = dict(config.get("payload") or {})
            payload.setdefault("schedule_id", str(schedule.id))
            scheduled_at = config.get("scheduled_at")
            cron = config.get("cron")
            if scheduled_at:
                run_date = datetime.fromisoformat(str(scheduled_at))
                if run_date.tzinfo is None:
                    run_date = run_date.replace(tzinfo=timezone.utc)
                else:
                    run_date = run_date.astimezone(timezone.utc)
                if run_date <= now:
                    continue
                desired.append((schedule, payload, run_date, None))
            elif cron:
                desired.append((schedule, payload, None, str(cron)))
            else:
                raise ValueError(f"TIME schedule {schedule.id} has no trigger")
            desired_ids.add(str(schedule.id))

        for job in self.scheduler.get_jobs():
            payload = dict((job.kwargs or {}).get("payload") or {})
            if payload.get("schedule_id") == job.id and job.id not in desired_ids:
                self.scheduler.remove_job(job.id)

        for schedule, payload, run_date, cron in desired:
            if run_date is not None:
                self.add_once_job(
                    schedule_id=schedule.id,
                    user_id=schedule.user_id,
                    run_date=run_date,
                    payload=payload,
                )
            else:
                assert cron is not None
                self.add_cron_job(
                    schedule_id=schedule.id,
                    user_id=schedule.user_id,
                    cron_expression=cron,
                    payload=payload,
                )

    async def shutdown(self, wait: bool = True):
        """Shutdown the scheduler and event emitter."""
        if self._started:
            self.scheduler.shutdown(wait=wait)
            self._started = False

            # Stop event emitter
            emitter = get_event_emitter()
            await emitter.stop()

            logger.info("APScheduler shutdown")

    def add_cron_job(
        self,
        schedule_id: UUID,
        user_id: UUID,
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
            kwargs={
                "schedule_id": job_id,
                "user_id": str(user_id),
                "payload": payload,
            },
            replace_existing=replace_existing,
        )

        logger.info(
            f"Added cron job {job_id} for schedule {schedule_id} with schedule: {cron_expression}"
        )

    def add_once_job(
        self,
        schedule_id: UUID,
        user_id: UUID,
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
            kwargs={
                "schedule_id": job_id,
                "user_id": str(user_id),
                "payload": payload,
            },
            replace_existing=replace_existing,
        )

        logger.info(
            f"Added one-time job {job_id} for schedule {schedule_id} scheduled for: {run_date}"
        )

    def remove_job(self, job_id: str) -> None:
        """Remove a job by ID."""
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to remove job {job_id}: {e}")

    def pause_job(self, job_id: str) -> None:
        """Pause a job."""
        try:
            self.scheduler.pause_job(job_id)
            logger.info(f"Paused job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to pause job {job_id}: {e}")

    def resume_job(self, job_id: str) -> None:
        """Resume a job."""
        try:
            self.scheduler.resume_job(job_id)
            logger.info(f"Resumed job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to resume job {job_id}: {e}")

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
