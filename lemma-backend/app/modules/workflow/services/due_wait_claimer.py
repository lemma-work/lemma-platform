"""Claiming due workflow WAIT_UNTIL timers, from the module that owns the table."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.domain.timers import (
    DEFAULT_TIMER_CLAIM_LIMIT,
    ClaimedTimer,
    lease_expiry,
    lease_is_free,
)
from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel

WORKFLOW_WAIT_SOURCE = "workflow_wait_until"


async def claim_due_workflow_waits(
    session,
    *,
    now: datetime,
    limit: int = DEFAULT_TIMER_CLAIM_LIMIT,
) -> list[ClaimedTimer]:
    """Take due `WAIT_UNTIL` timers. The caller commits to make the lease real."""
    statement = (
        select(WorkflowRunWaitModel)
        .where(
            WorkflowRunWaitModel.status == "ACTIVE",
            WorkflowRunWaitModel.wait_type == "TIME",
            WorkflowRunWaitModel.scheduled_at.is_not(None),
            WorkflowRunWaitModel.scheduled_at <= now,
            lease_is_free(WorkflowRunWaitModel.fire_lease_until, now),
        )
        .order_by(WorkflowRunWaitModel.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.scalars(statement)).all())

    claimed: list[ClaimedTimer] = []
    for row in rows:
        row.fire_lease_until = lease_expiry(now)
        # `external_ref` is the token the resume path matches on. A row without
        # one cannot be woken by reference, so leasing it would only hide it.
        if not row.external_ref:
            continue
        fire_at = row.scheduled_at.astimezone(timezone.utc)
        claimed.append(
            ClaimedTimer(
                timer_id=UUID(row.external_ref),
                user_id=None,
                fire_at=fire_at,
                payload={
                    "workflow_run_id": str(row.run_id),
                    "wait_ref": row.external_ref,
                    "scheduled_at": fire_at.isoformat(),
                    "source": WORKFLOW_WAIT_SOURCE,
                },
            )
        )
    return claimed
