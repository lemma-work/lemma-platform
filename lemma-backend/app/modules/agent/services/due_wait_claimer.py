"""Claiming due agent conversation waits, from the module that owns the table.

Every wait type is armed with a ``scheduled_at``, whatever else may resolve it,
so one claim serves all three. What a fired wait *means* differs by type and is
decided downstream by ``AgentWaitService.resolve``; the claim only says whose
turn it is to look.
"""

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
from app.modules.agent.infrastructure.models import AgentConversationWaitModel

WAIT_WAKE_SOURCE = "agent_wait"


async def claim_due_waits(
    session,
    *,
    now: datetime,
    limit: int = DEFAULT_TIMER_CLAIM_LIMIT,
) -> list[ClaimedTimer]:
    """Take due agent conversation waits of any type."""
    statement = (
        select(AgentConversationWaitModel)
        .where(
            AgentConversationWaitModel.status == "ACTIVE",
            AgentConversationWaitModel.scheduled_at.is_not(None),
            AgentConversationWaitModel.scheduled_at <= now,
            lease_is_free(AgentConversationWaitModel.fire_lease_until, now),
        )
        .order_by(AgentConversationWaitModel.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.scalars(statement)).all())

    claimed: list[ClaimedTimer] = []
    for row in rows:
        row.fire_lease_until = lease_expiry(now)
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
                    "source": WAIT_WAKE_SOURCE,
                },
            )
        )
    return claimed
