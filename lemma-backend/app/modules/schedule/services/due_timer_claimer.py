"""Claiming due one-shot timers: workflow `WAIT_UNTIL` and agent snoozes.

These are the timers APScheduler held that were *not* re-derivable from a
`Schedule` row -- which is why deleting the job store looked unsafe at first
glance. They are re-derivable; the target time simply lived somewhere nothing
could index. `workflow_run_waits.scheduled_at` and
`agent_conversation_waits.scheduled_at` fixed that, and this claims from them.

The claim differs from a schedule's. A schedule advances `next_fire_at` and the
next occurrence is a different one, so the cursor move *is* the claim. A timer
fires once and has nothing to advance, so a row lock alone would be released at
commit and the next tick would take the same row again. Hence a lease: claiming
stamps `fire_lease_until`, and the due query skips rows still holding one.

The dispatched payloads are rebuilt to match exactly what the scheduler adapters
used to send, because `_dispatch_wake` branches on their keys. Both were always
derivable from the wait row -- the adapters copied them out of arguments they
already had -- which is one more way the job store held nothing of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.models import AgentConversationWaitModel
from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel

logger = get_logger(__name__)

DEFAULT_TIMER_CLAIM_LIMIT = 100

#: How long a claim holds a timer before another replica may retry it. Long
#: enough that an ordinary dispatch finishes inside it, short enough that a
#: replica dying mid-fire delays the wake by seconds rather than minutes.
FIRE_LEASE_SECONDS = 60

SNOOZE_WAKE_SOURCE = "agent_snooze"
WORKFLOW_WAIT_SOURCE = "workflow_wait_until"


@dataclass(frozen=True, slots=True)
class ClaimedTimer:
    """One timer this replica owns for the length of its lease."""

    timer_id: UUID
    user_id: UUID | None
    fire_at: datetime
    payload: dict


def _lease_is_free(column, now: datetime):
    return or_(column.is_(None), column <= now)


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
            _lease_is_free(WorkflowRunWaitModel.fire_lease_until, now),
        )
        .order_by(WorkflowRunWaitModel.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.scalars(statement)).all())

    claimed: list[ClaimedTimer] = []
    for row in rows:
        row.fire_lease_until = now + timedelta(seconds=FIRE_LEASE_SECONDS)
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


async def claim_due_snooze_waits(
    session,
    *,
    now: datetime,
    limit: int = DEFAULT_TIMER_CLAIM_LIMIT,
) -> list[ClaimedTimer]:
    """Take due agent snooze timers."""
    statement = (
        select(AgentConversationWaitModel)
        .where(
            AgentConversationWaitModel.status == "ACTIVE",
            AgentConversationWaitModel.scheduled_at.is_not(None),
            AgentConversationWaitModel.scheduled_at <= now,
            _lease_is_free(AgentConversationWaitModel.fire_lease_until, now),
        )
        .order_by(AgentConversationWaitModel.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.scalars(statement)).all())

    claimed: list[ClaimedTimer] = []
    for row in rows:
        row.fire_lease_until = now + timedelta(seconds=FIRE_LEASE_SECONDS)
        if not row.external_ref:
            continue
        fire_at = row.scheduled_at.astimezone(timezone.utc)
        claimed.append(
            ClaimedTimer(
                timer_id=UUID(row.external_ref),
                user_id=None,
                fire_at=fire_at,
                payload={
                    "conversation_id": str(row.conversation_id),
                    "wait_ref": row.external_ref,
                    "scheduled_at": fire_at.isoformat(),
                    "source": SNOOZE_WAKE_SOURCE,
                },
            )
        )
    return claimed
