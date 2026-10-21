"""Which agents answer a line, in which conversation and run, and with what.

One row per line and agent: several agents may answer one line, and an agent
answers a line once. The row is written as the agent's turn starts, so a run
knows exactly which line it answers -- not the newest line its conversation
happens to have heard by the time it finishes -- and keeps the answer it
posted. A line folded into another channel takes its rows with it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class AnswerState(StrEnum):
    """Where an agent's answer to a line stands."""

    #: The turn has started; its run is working.
    ANSWERING = "ANSWERING"
    #: The run ended with an answer, posted as ``answer_message_id``.
    ANSWERED = "ANSWERED"
    #: The run ended without one: it failed, stopped, paused, or had nothing
    #: for the line's readers.
    ENDED = "ENDED"


class ChannelLineAnswerModel(Base):
    """An agent answering one line."""

    __tablename__ = "channel_line_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "channel_id"],
            ["channel_messages.id", "channel_messages.channel_id"],
            name="fk_channel_line_answers_line",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        CheckConstraint(
            "state IN ('ANSWERING', 'ANSWERED', 'ENDED')",
            name="ck_channel_line_answers_state",
        ),
        # The run being answered for, when it finishes.
        Index(
            "ix_channel_line_answers_run",
            "agent_run_id",
            postgresql_where=text("agent_run_id IS NOT NULL"),
        ),
        Index("ix_channel_line_answers_conversation", "conversation_id"),
        Index(
            "ix_channel_line_answers_answer",
            "answer_message_id",
            postgresql_where=text("answer_message_id IS NOT NULL"),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(primary_key=True)
    #: The agent answering: an agent's id, or the pod's id for the pod's own.
    agent_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=AnswerState.ANSWERING.value,
        server_default=text("'ANSWERING'"),
    )
    answer_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


__all__ = ["AnswerState", "ChannelLineAnswerModel"]
