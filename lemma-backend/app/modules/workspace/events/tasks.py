"""Periodic sandbox reclamation.

The sandbox manager ran this as a maintenance loop inside its own process.
Deleting it left `SandboxSweeper` written, tested and called by nothing, which
is the worst of both: idle sandboxes would hold compute forever, and a
container or paid E2B sandbox that outlived its row would never be found again.

It runs on the worker rather than the API because it is slow, bursty and must
not compete with a user waiting on a tool call.
"""

from __future__ import annotations

from app.modules.workspace.config import workspace_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import streaq_cron
from app.core.log.log import get_logger

logger = get_logger(__name__)


@streaq_cron(workspace_settings.sweep_cron, name="sweep_workspace_sandboxes")
async def sweep_workspace_sandboxes_task() -> None:
    from app.modules.workspace.services.sandbox_composition import get_sandbox_service
    from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper

    sweeper = SandboxSweeper(
        service=get_sandbox_service(),
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
    )

    idle_after = workspace_settings.idle_release_seconds
    if idle_after > 0:
        released = await sweeper.release_idle(idle_after_seconds=idle_after)
        if released:
            logger.info(
                "workspace.sandbox_sweeper.released_idle_sandboxes.observed",
                released_count=released,
            )

    reclaimed = await sweeper.reclaim_orphans()
    if reclaimed:
        logger.info(
            "workspace.sandbox_sweeper.reclaimed_orphaned_objects.observed",
            reclaimed_count=len(reclaimed),
        )
