"""``agent_run_steps``: the run log, one row per step (``domain/run_log``).

``messages`` is TEXT, never JSONB. JSONB re-sorts keys and re-spaces values,
which is exactly the drift this table exists to remove: the row must hand back
the bytes pydantic-ai wrote.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase
from app.modules.agent.domain.run_log import ReplayStep, RunStepKind
from app.modules.agent.infrastructure.models.typed_state import enum_column


class AgentRunStepModel(UUIDCreatedBase):
    """One step of a conversation's run log."""

    __tablename__ = "agent_run_steps"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_agent_run_step_seq"),
        Index("ix_agent_run_steps_conversation_seq", "conversation_id", "seq"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    #: The run that wrote it; None for nothing yet, kept nullable so a step
    #: outlives a run row removed with its retention.
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    #: Its place in the conversation's log, from 1.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[RunStepKind] = enum_column(RunStepKind, length=16)
    #: ``ModelMessagesTypeAdapter.dump_json`` of what the step added.
    messages: Mapped[str] = mapped_column(Text, nullable=False)

    def to_replay(self) -> ReplayStep:
        return ReplayStep(
            seq=self.seq, kind=self.kind, run_id=self.run_id, messages=self.messages
        )
