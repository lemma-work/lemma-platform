"""Conversations, the runs that answer them, and the messages in both.

These three are mutually referential -- a run names its conversation as a class,
and a conversation orders its messages and runs through lambdas closing over
theirs -- so they share a module rather than importing each other in a cycle."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase, UUIDCreatedBase
from app.modules.agent.domain.entities import (
    AgentRun as AgentRunEntity,
    Conversation as ConversationEntity,
    Message as MessageEntity,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentRunStatus,
    ConversationStatus,
    ConversationType,
    MessageKind,
)
from app.modules.agent.infrastructure.model_converters import (
    agent_runtime_from_json,
    default_agent_runtime,
)

from app.modules.agent.infrastructure.models.agent import AgentModel


class ConversationModel(UUIDAuditBase):
    """Conversation shared by the pod assistant and pod agents."""

    __tablename__ = "agent_conversations"
    __table_args__ = (
        Index(
            "ix_agent_conv_user_pod_roots",
            "user_id",
            "pod_id",
            "id",
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "ix_agent_conv_user_pod_agent_roots",
            "user_id",
            "pod_id",
            text("COALESCE(agent_id, '00000000-0000-0000-0000-000000000001'::uuid)"),
            "id",
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "ix_agent_conv_metadata",
            "conversation_metadata",
            postgresql_using="gin",
        ),
        Index(
            "uq_agent_conversation_origin",
            "origin_type",
            "origin_id",
            unique=True,
            postgresql_where=text("origin_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_runtime: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    origin_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origin_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ConversationType.CHAT.value,
        index=True,
    )
    status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    output_data: Mapped[dict | str | None] = mapped_column(JSONB, nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: Put away, not deleted: the row stays, the listing skips it, and a new
    #: message clears it (`append_message`).
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    owner: Mapped[Any] = relationship("User", foreign_keys=[user_id])
    pod: Mapped[Any] = relationship("Pod", foreign_keys=[pod_id])
    organization: Mapped[Any] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    agent: Mapped["AgentModel | None"] = relationship(
        "AgentModel",
        foreign_keys=[agent_id],
    )
    messages: Mapped[list["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="conversation",
        cascade="all, delete-orphan",
        # The FK already declares ON DELETE CASCADE, so the database removes
        # these rows itself. Without passive_deletes SQLAlchemy insists on
        # loading every child into the session first and deleting them one
        # at a time -- which on a large collection is a memory event, not a
        # slow query.
        passive_deletes=True,
        order_by=lambda: MessageModel.sequence,
        foreign_keys=lambda: [MessageModel.conversation_id],
    )
    agent_runs: Mapped[list["AgentRunModel"]] = relationship(
        "AgentRunModel",
        back_populates="conversation",
        cascade="all, delete-orphan",
        # The FK already declares ON DELETE CASCADE, so the database removes
        # these rows itself. Without passive_deletes SQLAlchemy insists on
        # loading every child into the session first and deleting them one
        # at a time -- which on a large collection is a memory event, not a
        # slow query.
        passive_deletes=True,
        order_by=lambda: AgentRunModel.created_at,
        foreign_keys=lambda: [AgentRunModel.conversation_id],
    )

    def __str__(self) -> str:
        return self.title or f"conversation {str(self.id)[:8]}"

    def to_entity(self) -> ConversationEntity:
        loaded_messages = self.__dict__.get("messages", [])
        loaded_runs = self.__dict__.get("agent_runs", [])
        latest_run = loaded_runs[-1] if loaded_runs else None
        return ConversationEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            user_id=self.user_id,
            pod_id=self.pod_id,
            organization_id=self.organization_id,
            agent_id=self.agent_id,
            title=self.title,
            instructions=self.instructions,
            agent_runtime=agent_runtime_from_json(self.agent_runtime),
            origin_type=self.origin_type,
            origin_id=self.origin_id,
            parent_id=self.parent_id,
            type=ConversationType(
                self.conversation_type or ConversationType.CHAT.value
            ),
            status=ConversationStatus(self.status)
            if self.status
            else ConversationStatus(latest_run.status)
            if latest_run
            else None,
            output=self.output_data,
            metadata=self.conversation_metadata,
            is_archived=bool(self.is_archived),  # None until the INSERT.
            last_run_status=latest_run.status if latest_run else None,
            last_run_error=latest_run.error if latest_run else None,
            last_run_finished_at=latest_run.finished_at if latest_run else None,
            messages=[message.to_entity() for message in loaded_messages],
            agent_runs=[agent_run.to_entity() for agent_run in loaded_runs],
        )


class AgentRunModel(UUIDAuditBase):
    """Internal execution attempt for a conversation."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_run_conversation_created", "conversation_id", "created_at"),
        Index("ix_agent_run_conversation_status", "conversation_id", "status"),
        Index(
            "uq_agent_active_run_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('RUNNING', 'STOP_REQUESTED', 'running', 'stop_requested')"
            ),
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AgentRunStatus.RUNNING.value,
        index=True,
    )
    agent_runtime: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=default_agent_runtime,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_data: Mapped[dict | str | None] = mapped_column(JSONB, nullable=True)
    run_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped["ConversationModel"] = relationship(
        ConversationModel,
        back_populates="agent_runs",
        foreign_keys=[conversation_id],
    )
    agent: Mapped["AgentModel | None"] = relationship(
        AgentModel,
        foreign_keys=[agent_id],
    )
    parent_run: Mapped["AgentRunModel | None"] = relationship(
        "AgentRunModel",
        remote_side="AgentRunModel.id",
        foreign_keys=[parent_run_id],
    )
    messages: Mapped[list["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="agent_run",
        order_by=lambda: MessageModel.sequence,
        foreign_keys=lambda: [MessageModel.agent_run_id],
    )

    def to_entity(self) -> AgentRunEntity:
        return AgentRunEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            conversation_id=self.conversation_id,
            agent_id=self.agent_id,
            parent_run_id=self.parent_run_id,
            status=AgentRunStatus(self.status),
            agent_runtime=AgentRuntimeConfig.model_validate(self.agent_runtime),
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
            output_data=self.output_data,
            metadata=self.run_metadata,
            messages=[
                message.to_entity() for message in self.__dict__.get("messages", [])
            ],
        )


class MessageModel(UUIDCreatedBase):
    """Append-only conversation message."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_agent_message_sequence"
        ),
        Index("ix_agent_message_conversation_sequence", "conversation_id", "sequence"),
        Index("ix_agent_message_run_sequence", "agent_run_id", "sequence"),
        Index("ix_agent_message_tool_call", "tool_call_id"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MessageKind.TEXT.value,
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_args: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=True
    )
    tool_result: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, nullable=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    conversation: Mapped["ConversationModel"] = relationship(
        ConversationModel,
        back_populates="messages",
        foreign_keys=[conversation_id],
    )
    agent_run: Mapped["AgentRunModel | None"] = relationship(
        AgentRunModel,
        back_populates="messages",
        foreign_keys=[agent_run_id],
    )

    def to_entity(self) -> MessageEntity:
        return MessageEntity(
            id=self.id,
            created_at=self.created_at,
            conversation_id=self.conversation_id,
            sequence=self.sequence,
            agent_run_id=self.agent_run_id,
            role=self.role,
            kind=MessageKind(self.kind),
            text=self.text,
            tool_name=self.tool_name,
            tool_call_id=self.tool_call_id,
            tool_args=self.tool_args,
            tool_result=self.tool_result,
            metadata=self.message_metadata,
        )
