"""``schedule.outcome``: a target's terminal state, recorded on its run.

FAST: one database transaction -- the run's outcome and the schedule's failure
streak (and the breaker's pause, with its notice item) commit together with
the item's deletion.
"""

from __future__ import annotations

from app.core.infrastructure.work import FastContext
from app.modules.schedule.work.kinds import TargetOutcome
from app.modules.workflow.contracts.run_work import (
    WorkflowRunFinished,
    WorkflowStartRefused,
)

_STATUSES = {
    "COMPLETED": "COMPLETED",
    "FAILED": "TARGET_FAILED",
    "CANCELLED": "CANCELLED",
    "STOPPED": "CANCELLED",
}


async def run_target_outcome(ctx: FastContext, payload: TargetOutcome) -> None:
    from app.modules.schedule.domain.schedule import ScheduleRunStatus
    from app.modules.schedule.services.run_outcome_service import (
        ScheduleRunOutcomeService,
    )

    mapped = _STATUSES.get(payload.status)
    if mapped is None:
        return
    status = ScheduleRunStatus(mapped)
    await ScheduleRunOutcomeService(ctx.uow).record_target_outcome(
        target_kind=payload.target_kind,
        target_run_id=payload.target_run_id,
        status=status,
        completed_at=payload.completed_at,
        error_type=payload.error_type
        or (
            f"{payload.target_kind.title()}RunFailed"
            if status is ScheduleRunStatus.TARGET_FAILED
            else None
        ),
    )


def started_by_schedule(fact: WorkflowRunFinished | WorkflowStartRefused) -> bool:
    """Only a schedule-started run names a schedule source (``<schedule>:<event>``)."""
    return bool(fact.schedule_event_id) and ":" in (fact.schedule_event_id or "")


async def run_workflow_finished(ctx: FastContext, payload: WorkflowRunFinished) -> None:
    """A workflow a schedule started reached a terminal state, in this transaction's wake."""
    from datetime import datetime, timezone

    await run_target_outcome(
        ctx,
        TargetOutcome(
            target_kind="WORKFLOW",
            target_run_id=str(payload.run_id),
            status=payload.status,
            completed_at=datetime.now(timezone.utc),
            error_type="WorkflowRunFailed" if payload.status == "FAILED" else None,
        ),
    )


async def run_workflow_refused(ctx: FastContext, payload: WorkflowStartRefused) -> None:
    """The workflow could not start the run at all: dead-letter it, and count it."""
    from app.modules.schedule.repositories.fire_ledger import FireLedger
    from app.modules.schedule.services.target_start import dead_letter_run

    run = await FireLedger(ctx.uow.session).by_target("WORKFLOW", str(payload.run_id))
    if run is None:
        return
    await dead_letter_run(ctx.uow, run.id, error_type="WorkflowStartRefused")
