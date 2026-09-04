"""The spend reservation an in-flight run is holding, kept on its row.

A run reserves against its spend counters before it starts and releases at the
end, but the handle lived only in the worker's memory -- so a SIGKILLed worker
stranded the reservation until the window rolled over, permanently shrinking
that person's allowance in the meantime. `reconcile_orphaned_agent_runs` is the
process that cleans up after a dead worker and had nothing to release.

Free functions over a session rather than methods, because
`ConversationRepository` is a file whose length is ratcheted and none of this
is about conversations.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel


async def store_usage_reservation(
    session: AsyncSession, *, agent_run_id: UUID, reservation: JsonObject | None
) -> None:
    """Record (or clear) the spend reservation this run is holding."""
    await session.execute(
        update(AgentRunModel)
        .where(AgentRunModel.id == agent_run_id)
        .values(usage_reservation=reservation)
    )


async def claim_usage_reservation(
    session: AsyncSession, *, agent_run_id: UUID
) -> JsonObject | None:
    """Take the run's reservation, leaving nothing behind for a second taker.

    Locked read then clear, in the caller's transaction: the worker and the
    orphan reconciler can both reach for the same reservation, and releasing it
    twice would hand back allowance that was only ever taken once. Whoever wins
    the row lock gets the handle; the loser reads ``None`` and does nothing.

    ``RETURNING`` on the update would not do -- Postgres returns the *new* value
    of the column, which is the null we just wrote.
    """
    result = await session.execute(
        select(AgentRunModel.usage_reservation)
        .where(
            AgentRunModel.id == agent_run_id,
            AgentRunModel.usage_reservation.is_not(None),
        )
        .with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        return None
    await store_usage_reservation(session, agent_run_id=agent_run_id, reservation=None)
    return reservation
