"""Periodic sandbox reclamation.

The sandbox manager ran this as a maintenance loop inside its own process.
Deleting it left `SandboxSweeper` written, tested and called by nothing, which
is the worst of both: idle sandboxes would hold compute forever, and a
container or paid E2B sandbox that outlived its row would never be found again.

It runs on the worker rather than the API because it is slow, bursty and must
not compete with a user waiting on a tool call.
"""

from __future__ import annotations

from datetime import timedelta

from app.modules.workspace.config import workspace_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkClass, periodic_job
from app.core.log.log import get_logger

logger = get_logger(__name__)


# Automation, not admin: it talks to the sandbox provider.
@periodic_job(
    "workspace.sandboxes_sweep",
    every=timedelta(seconds=workspace_settings.sweep_interval_seconds),
    cls=WorkClass.AUTOMATION,
)
async def sweep_workspace_sandboxes_task() -> None:
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service
    from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper

    from app.modules.workspace.services import compute_meter, machine_housekeeping

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    sweeper = SandboxSweeper(
        service=get_sandbox_service(),
        uow_factory=uow_factory,
        release=machine_housekeeping.machine_release(uow_factory),
        held=machine_housekeeping.slot_held,
    )

    # Metering before release, so a machine released this tick has its last
    # slice written by the release rather than skipped by both.
    tick = await compute_meter.meter_running(uow_factory)
    if tick.resumed:
        await machine_housekeeping.after_resume(uow_factory, tick.resumed)
    await compute_meter.enforce(uow_factory)

    idle_after = workspace_settings.idle_release_seconds
    if idle_after > 0:
        released = await sweeper.release_idle(idle_after_seconds=idle_after)
        if released:
            logger.info(
                "workspace.sandbox_sweeper.released_idle_sandboxes.observed",
                released_count=released,
            )

    await machine_housekeeping.daily_login_copies(uow_factory, tick.running)

    reclaimed = await sweeper.reclaim_orphans()
    if reclaimed:
        logger.info(
            "workspace.sandbox_sweeper.reclaimed_orphaned_objects.observed",
            reclaimed_count=len(reclaimed),
        )
