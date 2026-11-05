"""Dropping what the agent's model was sent, when what it read has changed.

A run's model history is logged as it was sent (``domain/run_log``). Some of
what it read is the projection of something another module owns -- a
channel's lines, shown as background; a contact's words -- and when the owner
changes or forgets it, the logged copy must not outlive it. The owner calls
this in the transaction that makes the change; the conversations it names
take their past in again from the rows on their next run.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.db.run_steps import RunSteps


async def forget_model_history(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    reading_channels: Sequence[UUID] | None = None,
) -> None:
    """Drop the logged model history of the pod's conversations: every one,
    or those shown a background from ``reading_channels``."""
    await RunSteps(uow).forget(pod_id=pod_id, reading_channels=reading_channels)


__all__ = ["forget_model_history"]
