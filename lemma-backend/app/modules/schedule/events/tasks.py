"""Periodic maintenance owned by the schedule module."""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from datetime import timedelta

from app.core.infrastructure.work import WorkClass, periodic_job
from app.core.log.log import get_logger

logger = get_logger(__name__)


# Hourly, in the admin class. The sweep is slow and bursty by design -- it
# drains until a short batch or its wall-clock budget stops it -- and must not
# compete with a schedule fire or a user waiting on a tool call.
@periodic_job("schedule.runs_prune", every=timedelta(hours=1))
async def prune_schedule_runs_task() -> None:
    from app.modules.schedule.infrastructure.run_retention import prune_schedule_runs

    removed = await prune_schedule_runs(async_session_maker)
    if removed:
        logger.info("schedule.runs.pruned", deleted_count=removed)


# Every quarter hour: an orphaned subscription costs a renewal call a few times
# a day, and an author who left the pod is caught within the quarter.
# Automation, not admin: it unsubscribes at external MCP servers.
@periodic_job(
    "schedule.mcp_listening_reconcile",
    every=timedelta(minutes=15),
    cls=WorkClass.AUTOMATION,
)
async def reconcile_mcp_listening_task() -> None:
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.connectors.contracts.mcp_events import (
        mcp_listening_page,
        unsubscribe_from_mcp_event,
    )
    from app.modules.schedule.services.mcp_listening_reconciler import (
        McpListeningReconciler,
    )

    await McpListeningReconciler(
        SessionUnitOfWorkFactory(async_session_maker),
        page=mcp_listening_page,
        unsubscribe=unsubscribe_from_mcp_event,
    ).run()
