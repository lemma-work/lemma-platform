"""A conversation suspended waiting on somebody or on the clock."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class AgentConversationWaitModel(UUIDAuditBase):
    """What a waiting conversation is waiting on — the single source of truth.

    Mirrors ``workflow_run_waits``. The partial unique index enforces at most one
    ACTIVE wait per conversation: a turn that paused on a wait cannot also be
    made to wait again until it resolves, and a duplicate wake cannot create a
    second row.
    """

    __tablename__ = "agent_conversation_waits"
    __table_args__ = (
        Index(
            "ix_agent_conversation_waits_conversation_status",
            "conversation_id",
            "status",
        ),
        Index("ix_agent_conversation_waits_external_ref", "external_ref"),
        Index(
            "ix_agent_conversation_waits_type_ref_status",
            "wait_type",
            "external_ref",
            "status",
        ),
        Index(
            "uq_agent_conversation_waits_one_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)

    wait_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    # When the wait's work item is due: informational ("wakes at"); the item
    # is what fires it.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Nullable to match the migration: `create` always writes a dict, but a row
    # inserted by hand or by a future backfill must not need one.
    spec: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_entity(self):
        from app.modules.agent.domain.wait import (
            AgentConversationWaitEntity,
            AgentWaitStatus,
            AgentWaitType,
        )

        return AgentConversationWaitEntity(
            id=self.id,
            conversation_id=self.conversation_id,
            agent_run_id=self.agent_run_id,
            pod_id=self.pod_id,
            tool_call_id=self.tool_call_id,
            wait_type=AgentWaitType(self.wait_type),
            status=AgentWaitStatus(self.status),
            external_ref=self.external_ref,
            scheduled_at=self.scheduled_at,
            spec=dict(self.spec or {}),
            completed_at=self.completed_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


AgentConversationWait = AgentConversationWaitModel
