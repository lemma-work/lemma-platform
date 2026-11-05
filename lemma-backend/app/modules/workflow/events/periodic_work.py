"""The workflow module's periodic work.

* ``workflow.waits_prune`` (1 h, admin) -- finished machine waits.

Worker only (``register_periodic_work``).
"""

from __future__ import annotations

from datetime import timedelta

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import periodic_job
from app.core.log.log import get_logger
from app.modules.workflow.config import workflow_settings

logger = get_logger(__name__)


@periodic_job("workflow.waits_prune", every=timedelta(hours=1))
async def prune_workflow_run_waits() -> None:
    """Reclaim finished machine waits. Human approvals are never touched.

    A wait row is scaffolding for one step: the engine records what a run is
    blocked on, and once the function returns or the agent finishes, the row has
    served its purpose. Nothing removed them, so they accumulated -- in
    production, roughly 105,000 machine waits against 5,700 human ones.

    ``HUMAN`` waits are excluded by predicate at any age and any status. Those
    are the record of who was asked to approve something and what they said,
    which is not scaffolding and not ours to age out. If that distinction ever
    stops holding, this sweep is the thing that must change, not the retention
    window.

    Offset off the hour to stay clear of the other delete-heavy sweeps.
    """
    from app.modules.workflow.infrastructure.repositories.wait_retention import (
        prune_terminal_machine_waits,
    )

    deleted = await prune_terminal_machine_waits(
        async_session_maker,
        retention_days=workflow_settings.workflow_wait_retention_days,
        batch_size=workflow_settings.workflow_wait_retention_batch_size,
        budget_seconds=workflow_settings.workflow_wait_retention_budget_seconds,
    )
    if deleted:
        logger.debug(
            "workflow.handlers.prune_workflow_run_waits.observed",
            deleted_count=deleted,
        )
