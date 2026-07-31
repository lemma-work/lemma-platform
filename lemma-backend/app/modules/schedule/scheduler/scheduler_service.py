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
from apscheduler.events import EVENT_JOB_ERROR
from pytz import utc
from sqlalchemy import select
from sqlalchemy.engine import make_url

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
    user_id: str | None = None,
    payload: dict | None = None,
):
    """Static function to execute scheduled jobs.

    This function is called by APScheduler when a job fires.
    It must be a module-level function to be serializable.

    ``user_id`` is optional only because the job store is durable across
    deployments: jobs persisted before ownership existed carry no user_id and
    would otherwise raise TypeError on their first fire. Logical schedule jobs
    are rewritten with an owner by ``reconcile_time_schedule_jobs`` at startup;
    workflow wait timers are not, so they fire owner-less exactly once and the
    workflow run itself supplies the owner downstream.

    Args:
        schedule_id: The schedule ID as a string (will be converted to UUID)
        user_id: Owner of the resulting run; absent only on pre-ownership jobs
        payload: Optional payload data
    """
    from uuid import UUID

    emitter = get_event_emitter()

    schedule_uuid = UUID(schedule_id)
    await emitter.emit_scheduled_job_event(
        schedule_id=schedule_uuid,
        user_id=UUID(user_id) if user_id else None,
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
            logger.info("schedule.scheduler.started")
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

    async def reconcile_time_schedule_jobs(self) -> None:
        """Replace logical TIME jobs from authoritative schedule rows.

        Workflow wait timers are not logical schedule rows and carry a
        ``workflow_run_id`` payload instead of ``payload.schedule_id``; they are
        deliberately left untouched.

        One unusable row must never stop the fleet: a schedule whose config has
        no usable trigger is skipped like a past-due one-shot, so its stale job
        is dropped and every other schedule still reconciles. This mirrors
        ``SchedulerAPIClient.schedule_job``, which already skips the same rows at
        write time instead of failing the request.
        """
        schedules = await load_active_time_schedules()

        now = datetime.now(timezone.utc)
        desired: list[
            tuple[TimeScheduleJob, dict, datetime | None, str | None]
        ] = []
        for schedule in schedules:
            resolved = self._resolve_time_trigger(schedule, now=now)
            if resolved is None:
                continue
            payload, run_date, cron = resolved
            desired.append((schedule, payload, run_date, cron))

        # Write first, then prune against what actually landed. A row whose
        # trigger APScheduler rejects is treated exactly like one with no
        # trigger at all, so its stale job is dropped rather than left behind
        # firing pre-deploy kwargs. The scheduler is paused throughout, so no
        # job can fire against a half-reconciled store.
        scheduled_ids: set[str] = set()
        for schedule, payload, run_date, cron in desired:
            try:
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
            except (ValueError, TypeError):
                logger.warning(
                    "schedule.reconcile.unusable_row",
                    schedule_id=str(schedule.id),
                    reason="rejected_trigger",
                )
                continue
            scheduled_ids.add(str(schedule.id))

        for job in self.scheduler.get_jobs():
            payload = dict((job.kwargs or {}).get("payload") or {})
            if payload.get("schedule_id") == job.id and job.id not in scheduled_ids:
                self.scheduler.remove_job(job.id)

    def _resolve_time_trigger(
        self,
        schedule: TimeScheduleJob,
        *,
        now: datetime,
    ) -> tuple[dict, datetime | None, str | None] | None:
        """Return the desired job for one row, or None to drop it.

        None means "no job should exist for this schedule": the row is past due,
        has no usable trigger, or stores a run date this process cannot parse.
        """
        config = dict(schedule.config or {})
        payload = dict(config.get("payload") or {})
        payload.setdefault("schedule_id", str(schedule.id))
        scheduled_at = config.get("scheduled_at")
        cron = config.get("cron")

        if scheduled_at:
            try:
                run_date = datetime.fromisoformat(str(scheduled_at))
            except ValueError:
                logger.warning(
                    "schedule.reconcile.unusable_row",
                    schedule_id=str(schedule.id),
                    reason="unparsable_scheduled_at",
                )
                return None
            if run_date.tzinfo is None:
                run_date = run_date.replace(tzinfo=timezone.utc)
            else:
                run_date = run_date.astimezone(timezone.utc)
            if run_date <= now:
                return None
            return payload, run_date, None

        if cron:
            return payload, None, str(cron)

        logger.warning(
            "schedule.reconcile.unusable_row",
            schedule_id=str(schedule.id),
            reason="no_trigger",
        )
        return None

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

        logger.debug(
            "schedule.scheduler_service.added_cron_job_schedule_schedule.observed",
            job_id=job_id,
            schedule_id=schedule_id,
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
