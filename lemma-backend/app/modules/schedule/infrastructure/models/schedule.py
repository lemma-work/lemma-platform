from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)

class Schedule(UUIDAuditBase):
    """Unified schedule model for time, webhook, and datastore event sources."""

    __tablename__ = "schedules"

    # Owner
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Schedule source type and config
    schedule_type: Mapped[ScheduleType] = mapped_column(
        "schedule_type",
        SQLEnum(ScheduleType, native_enum=False, length=50),
        index=True,
    )
    workflow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflow_flows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # For WEBHOOK schedules: reference to app connector
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    connector_trigger_id: Mapped[str | None] = mapped_column(
        ForeignKey("connector_triggers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Type-specific config (JSON) - GIN indexed for querying
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    filter_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    filter_output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    visibility: Mapped[str] = mapped_column(String(30), default="POD", nullable=False)
    # Active status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Internal schedules are created by flow execution (for waits/timeouts)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Fire telemetry — last attempt outcome, for debuggability
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_fire_status: Mapped[ScheduleFireStatus | None] = mapped_column(
        SQLEnum(ScheduleFireStatus, native_enum=False, length=20), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Relationships
    user: Mapped[Any] = relationship("User", foreign_keys=[user_id])
    pod: Mapped[Any] = relationship("Pod", foreign_keys=[pod_id])
    workflow: Mapped[Any] = relationship(
        "WorkflowModel", foreign_keys=[workflow_id]
    )
    agent: Mapped[Any] = relationship(
        "AgentModel", foreign_keys=[agent_id]
    )

    __table_args__ = (
        Index("ix_schedules_user_pod", "user_id", "pod_id"),
        Index("ix_schedules_account", "account_id"),
        Index("ix_schedules_connector_trigger", "connector_trigger_id"),
        Index("ix_schedules_type_active", "schedule_type", "is_active"),
        Index("ix_schedules_workflow", "workflow_id"),
        Index("ix_schedules_agent", "agent_id"),
        Index("ix_schedules_config_gin", "config", postgresql_using="gin"),
        Index(
            "uq_schedules_pod_name",
            "pod_id",
            "name",
            unique=True,
            postgresql_where=text(
                "pod_id IS NOT NULL AND name IS NOT NULL AND is_internal = false"
            ),
        ),
    )

    def to_entity(self) -> ScheduleEntity:
        workflow = self.__dict__.get("workflow")
        agent = self.__dict__.get("agent")
        return ScheduleEntity.model_validate(self).model_copy(
            update={
                "workflow_name": workflow.name if workflow else None,
                "agent_name": agent.name if agent else None,
            }
        )

    def __repr__(self) -> str:
        return f"<Schedule(id={self.id}, name={self.name}, type={self.schedule_type}, active={self.is_active})>"
