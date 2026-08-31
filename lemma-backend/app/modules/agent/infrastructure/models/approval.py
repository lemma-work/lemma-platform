"""Approval decisions and the feedback a run leaves behind."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase, UUIDCreatedBase

from app.modules.agent.infrastructure.models.agent import AgentModel


class AgentApprovalDecisionModel(UUIDCreatedBase):
    """Durable record of a user's decision on a ``request_approval`` tool call.

    The approval card (the pending ``request_approval`` tool call) lives in the
    message log; this row captures the user's resolution so the paused tool can
    read it after waking, independent of pub/sub timing or worker restarts.
    """

    __tablename__ = "agent_approval_decisions"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "approval_id", name="uq_agent_approval_decision"
        ),
        Index(
            "ix_agent_approval_decision_conversation",
            "conversation_id",
            "approval_id",
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    approval_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Claimed before the approved tool runs, so a retried reconcile job cannot
    # run it twice. See `claim_approval_execution` for why a read was not enough.
    execution_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentFeedbackModel(UUIDAuditBase):
    """Feedback reports submitted through Agent tools."""

    __tablename__ = "agent_feedback"
    __table_args__ = (
        Index("ix_agent_feedback_user_created", "user_id", "created_at"),
        Index("ix_agent_feedback_agent_created", "agent_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_encountered: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    actual_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_next_steps: Mapped[str | None] = mapped_column(Text, nullable=True)

    reporter: Mapped[Any] = relationship("User", foreign_keys=[user_id])
    agent: Mapped["AgentModel | None"] = relationship(
        AgentModel,
        foreign_keys=[agent_id],
    )


AgentFeedback = AgentFeedbackModel
