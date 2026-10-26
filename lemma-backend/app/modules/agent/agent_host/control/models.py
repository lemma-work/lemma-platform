"""What the Agent Host keeps per conversation and per run, in its own tables.

Both were keys of the conversation's and the run's metadata JSON until
migration 0065: ``agent_host_provider_session_id`` on the conversation, and
``execution``, ``host_native_commands`` and ``final_answer`` on the run. The
keys stay in the JSON for one release; nothing reads or writes them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentHostSessionModel(Base):
    """The provider session a conversation talks to, and what it has been sent.

    ``session_memory`` documents each field. A row can hold only a pending
    promise: a turn records its instructions before the host has opened any
    session.
    """

    __tablename__ = "agent_host_sessions"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    harness_id: Mapped[UUID | None] = mapped_column(nullable=True)
    host_id: Mapped[UUID | None] = mapped_column(nullable=True)
    #: The run whose checkpoint bound the session, and when that run began.
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    run_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    host_cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_instructions_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pending_instructions_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    pending_prompt_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pending_prompt_seen_through: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    prompted_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    prompted_through: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AgentHostRunRecordModel(Base):
    """What one run decided once and every later context build reads back.

    ``execution`` is where its commands run (``host_execution_selection``),
    ``host_native_commands`` whether its agent's own shell replaces Lemma's
    command tools (``native_commands``), ``final_answer`` what its final-answer
    tool recorded (``final_answer``). Each is written alone, so one writer never
    disturbs another's.
    """

    __tablename__ = "agent_host_run_records"

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    execution: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    host_native_commands: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    final_answer: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
