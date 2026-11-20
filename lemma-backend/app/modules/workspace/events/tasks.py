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
    from app.modules.workspace.services import machine_housekeeping
    from app.modules.workspace.services.machine_sweep import run_steps, sweep_steps
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service
    from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    service = get_sandbox_service()
    sweeper = SandboxSweeper(
        service=service,
        uow_factory=uow_factory,
        release=machine_housekeeping.machine_release(uow_factory),
        held=machine_housekeeping.slot_held,
        idle_service=machine_housekeeping.service_idle(uow_factory),
    )
    await run_steps(sweep_steps(uow_factory, service=service, sweeper=sweeper))


# Off the agent's command path: a run's first command only measures the disk,
# and pruning -- up to three scopes of ten minutes each -- happens here.
@periodic_job(
    "workspace.machine_disk_janitor",
    every=timedelta(minutes=10),
    cls=WorkClass.AUTOMATION,
    max_runtime=timedelta(minutes=45),
)
async def machine_disk_janitor_task() -> None:
    from app.modules.workspace.services.disk_janitor import janitor_pass

    await janitor_pass(SessionUnitOfWorkFactory(async_session_maker))


# Its own job, not a step of the sweep: one move can take three hours, and
# each step is resumed from the state on the machine row.
@periodic_job(
    "workspace.machine_size_migrations",
    every=timedelta(minutes=10),
    cls=WorkClass.AUTOMATION,
    max_runtime=timedelta(hours=4, minutes=15),
)
async def machine_size_migrations_task() -> None:
    if not workspace_settings.machine_size_migration_enabled:
        return
    from app.modules.workspace.services.size_migration import run_pending_moves

    await run_pending_moves(SessionUnitOfWorkFactory(async_session_maker))
