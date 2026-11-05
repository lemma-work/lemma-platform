"""What the agent module's work items carry: ids, never snapshots.

Separate from the handlers so a writer -- the API starting a turn, the finalize
staging its follow-ups -- can build a payload without importing the runner and
everything it drags in. Validated when written and again when run.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AgentTurnWork(BaseModel):
    """One turn: the run to execute, and who it runs for."""

    run_id: UUID
    conversation_id: UUID
    user_id: UUID
    agent_name: str | None = None


class ConversationWork(BaseModel):
    """A follow-up keyed on a conversation (and, when it has one, a run)."""

    model_config = ConfigDict(extra="ignore")

    conversation_id: UUID
    run_id: UUID | None = None


class RepliesArrived(BaseModel):
    """Every question one batch of a conversation asked has closed."""

    conversation_id: UUID
    pod_id: UUID
    batch_id: str | None = None


class MemoryPassWork(BaseModel):
    """A finished run whose memory is kept after it, and who it worked for."""

    conversation_id: UUID
    run_id: UUID
    user_id: UUID


__all__ = ["AgentTurnWork", "ConversationWork", "MemoryPassWork", "RepliesArrived"]
