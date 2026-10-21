"""``schedule.filter``: the LLM filter as a job; the logic is ``services.trigger_filter``."""

from __future__ import annotations

from app.core.infrastructure.work import DeadContext, JobContext
from app.modules.schedule.work.kinds import FilterEvent


async def run_filter(ctx: JobContext, payload: FilterEvent) -> None:
    from app.modules.schedule.services.trigger_filter import run_schedule_filter

    await run_schedule_filter(ctx, payload)


async def on_filter_dead(ctx: DeadContext) -> None:
    from app.modules.schedule.services.trigger_filter import on_filter_dead as dead

    await dead(ctx)
