"""The feedback a run leaves behind."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase

from app.modules.agent.infrastructure.models.agent import AgentModel


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
