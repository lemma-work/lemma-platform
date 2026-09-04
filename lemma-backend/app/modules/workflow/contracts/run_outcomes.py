"""Whether a batch of workflow runs has finished, for a ledger that dispatched them.

A projection, not the rows. `WorkflowRunModel` carries four JSONB columns
including `step_history`, which grows with every step the run took; the caller
needs a status and a timestamp, and loading whole rows to read two scalars is
what put an unbounded payload on a five-minute cron.

One query for the whole batch rather than one per run, and the answer is the
mapping itself: a run id that is absent has no row, which is precisely the
distinction the caller acts on -- a target still running is left alone, a target
that is gone is redelivered or abandoned.

A submodule rather than `contracts/__init__`, like its siblings elsewhere: this
reaches the model layer, and `contracts/__init__` is imported by anything that
wants any contract at all.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome
from app.modules.workflow.infrastructure.models import WorkflowRunModel


async def load_run_outcomes(
    uow: SqlAlchemyUnitOfWork, run_ids: Collection[UUID]
) -> dict[UUID, TargetRunOutcome]:
    """The state of every named run that still exists."""
    if not run_ids:
        return {}
    rows = await uow.session.execute(
        select(
            WorkflowRunModel.id,
            WorkflowRunModel.status,
            WorkflowRunModel.completed_at,
        ).where(WorkflowRunModel.id.in_(set(run_ids)))
    )
    return {
        run_id: TargetRunOutcome(status=status, ended_at=completed_at)
        for run_id, status, completed_at in rows.all()
    }


__all__ = ["load_run_outcomes"]
