"""APScheduler executor context for deterministic scheduled invocations."""

from __future__ import annotations

import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.util import iscoroutinefunction_partial


_scheduled_run_time: ContextVar[datetime | None] = ContextVar(
    "schedule_scheduled_run_time",
    default=None,
)


def current_scheduled_run_time() -> datetime:
    """Return the APScheduler slot for the currently executing job."""
    scheduled_at = _scheduled_run_time.get()
    if scheduled_at is None:
        raise RuntimeError("Scheduled job is executing without an APScheduler slot")
    return scheduled_at


async def _run_coroutine_job_with_slot(job, jobstore_alias, run_times, logger_name):
    """APScheduler's coroutine runner with the exact fire slot in a context var."""
    events = []
    logger = logging.getLogger(logger_name)
    for run_time in run_times:
        if job.misfire_grace_time is not None:
            difference = datetime.now(timezone.utc) - run_time
            if difference > timedelta(seconds=job.misfire_grace_time):
                events.append(
                    JobExecutionEvent(
                        EVENT_JOB_MISSED,
                        job.id,
                        jobstore_alias,
                        run_time,
                    )
                )
                logger.warning('Run time of job "%s" was missed by %s', job, difference)
                continue

        token = _scheduled_run_time.set(run_time)
        try:
            retval = await job.func(*job.args, **job.kwargs)
        except BaseException as exc:
            _, _, tb = sys.exc_info()
            formatted_tb = "".join(traceback.format_tb(tb)) if tb is not None else ""
            events.append(
                JobExecutionEvent(
                    EVENT_JOB_ERROR,
                    job.id,
                    jobstore_alias,
                    run_time,
                    exception=exc,
                    traceback=formatted_tb,
                )
            )
            logger.exception('Job "%s" raised an exception', job)
            if tb is not None:
                traceback.clear_frames(tb)
        else:
            events.append(
                JobExecutionEvent(
                    EVENT_JOB_EXECUTED,
                    job.id,
                    jobstore_alias,
                    run_time,
                    retval=retval,
                )
            )
        finally:
            _scheduled_run_time.reset(token)
    return events


class ScheduledTimeAsyncIOExecutor(AsyncIOExecutor):
    """Async executor that exposes APScheduler's actual scheduled run time."""

    def _do_submit_job(self, job, run_times) -> None:
        if not iscoroutinefunction_partial(job.func):
            return super()._do_submit_job(job, run_times)

        def callback(future) -> None:
            self._pending_futures.discard(future)
            try:
                events = future.result()
            except BaseException:
                self._run_job_error(job.id, *sys.exc_info()[1:])
            else:
                self._run_job_success(job.id, events)

        coroutine = _run_coroutine_job_with_slot(
            job,
            job._jobstore_alias,
            run_times,
            self._logger.name,
        )
        future = self._eventloop.create_task(coroutine)
        future.add_done_callback(callback)
        self._pending_futures.add(future)
