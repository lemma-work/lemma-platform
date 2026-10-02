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


# Every minute: a digest's cadence is at least fifteen, so a minute late is the
# worst it runs. Every replica's tick is safe -- the claim decides who sends.
@streaq_cron("* * * * *", name="dispatch_schedule_digests")
async def dispatch_schedule_digests_task() -> None:
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.schedule.services.digest_dispatcher import dispatch_due_digests

    sent = await dispatch_due_digests(SessionUnitOfWorkFactory(async_session_maker))
    if sent:
        logger.info("schedule.digests.dispatched", digest_count=sent)
