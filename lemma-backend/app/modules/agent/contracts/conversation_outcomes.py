"""Whether a batch of conversations has finished, for a ledger that started them.

The agent-side twin of `workflow/contracts/run_outcomes.py`, and the same
projection for the same reason: the caller reconciles a status and a timestamp,
and a conversation row carries the output and the metadata it has accumulated.

`updated_at` stands in for an end time. A conversation has no `completed_at`
column -- it is a long-lived thing that stops being run rather than a run that
ends -- so the last write to it is the closest the ledger can get, and it is
only read at all once the status says the conversation is terminal.

A submodule rather than `contracts/__init__`, like its siblings elsewhere: this
reaches the model layer, and `contracts/__init__` is imported by anything that
wants any contract at all.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.schedule.contracts.target_outcome import TargetRunOutcome


async def load_conversation_outcomes(
    uow: SqlAlchemyUnitOfWork, conversation_ids: Collection[UUID]
) -> dict[UUID, TargetRunOutcome]:
    """The state of every named conversation that still exists."""
    if not conversation_ids:
        return {}
    rows = await uow.session.execute(
        select(
            ConversationModel.id,
            ConversationModel.status,
            ConversationModel.updated_at,
        ).where(ConversationModel.id.in_(set(conversation_ids)))
    )
    return {
        conversation_id: TargetRunOutcome(status=status, ended_at=updated_at)
        for conversation_id, status, updated_at in rows.all()
    }


__all__ = ["load_conversation_outcomes"]
