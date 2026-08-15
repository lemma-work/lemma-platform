"""Periodic maintenance owned by the schedule module."""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger

logger = get_logger(__name__)


# Hourly, and on the bulk lane. The sweep is slow and bursty by design -- it
# drains until a short batch or its wall-clock budget stops it -- and must not
# compete with a schedule fire or a user waiting on a tool call.
@streaq_cron("20 * * * *", name="prune_schedule_runs", lane=Lane.BULK)
async def prune_schedule_runs_task() -> None:
    from app.modules.schedule.infrastructure.run_retention import prune_schedule_runs

    removed = await prune_schedule_runs(async_session_maker)
    if removed:
        logger.info("schedule.runs.pruned", deleted_count=removed)
