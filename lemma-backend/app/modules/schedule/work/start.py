"""``schedule.start_target``: start a reserved run's target, once.

A JOB: starting an agent run is database writes today, but a workflow start
and authorization may grow I/O, and the per-trigger fire rate
(``trigger_fire``) is drawn at this kind's claim.
"""

from __future__ import annotations

from app.core.infrastructure.work import DeadContext, JobContext
from app.modules.schedule.work.kinds import StartTarget


async def run_start_target(ctx: JobContext, payload: StartTarget) -> None:
    from app.modules.schedule.services.target_start import start_reserved_run

    async with ctx.complete_work() as uow:
        await start_reserved_run(uow, payload.schedule_run_id)


async def on_start_dead(ctx: DeadContext) -> None:
    """Gave up: the run is dead-lettered with why, and the breaker counts it."""
    from app.modules.schedule.services.target_start import dead_letter_run

    try:
        payload = StartTarget.model_validate(ctx.item.payload)
    except ValueError:
        return
    error = (ctx.last_error or ctx.reason).split(":", 1)[0][:200]
    await dead_letter_run(ctx.uow, payload.schedule_run_id, error_type=error)
