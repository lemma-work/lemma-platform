"""Usage SQLAlchemy models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.usage.domain.entities import UsageRecord as UsageRecordEntity


class UsageRecord(UUIDAuditBase):
    """One compact usage record."""

    __tablename__ = "usage_records"

    organization_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    pod_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    user_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    parent_agent_run_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    source_type: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    profile_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    profile_scope: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    usage_kind: Mapped[str] = mapped_column(
        String(40),
        index=True,
        nullable=False,
        default="llm",
    )
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    units: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    record_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_usage_org_time", "organization_id", "occurred_at"),
        Index("ix_usage_pod_time", "pod_id", "occurred_at"),
        Index("ix_usage_user_time", "user_id", "occurred_at"),
        Index("ix_usage_agent_time", "agent_id", "occurred_at"),
        Index("ix_usage_agent_run_time", "agent_run_id", "occurred_at"),
        Index("ix_usage_org_profile_time", "organization_id", "profile_id", "occurred_at"),
        Index(
            "ix_usage_org_profile_scope_time",
            "organization_id",
            "profile_scope",
            "occurred_at",
        ),
        Index(
            "ix_usage_org_user_profile_scope_time",
            "organization_id",
            "user_id",
            "profile_scope",
            "occurred_at",
        ),
        Index("ix_usage_source_time", "source_type", "source_id", "occurred_at"),
    )

    def to_entity(self) -> UsageRecordEntity:
        return UsageRecordEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            organization_id=self.organization_id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            conversation_id=self.conversation_id,
            agent_run_id=self.agent_run_id,
            parent_agent_run_id=self.parent_agent_run_id,
            source_type=self.source_type,
            source_id=self.source_id,
            profile_id=self.profile_id,
            profile_scope=self.profile_scope,
            model_name=self.model_name,
            usage_kind=self.usage_kind,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            units=self.units,
            cost_usd=self.cost_usd,
            status=self.status,
            metadata=self.record_metadata or {},
            occurred_at=self.occurred_at,
        )

    @classmethod
    def from_entity(cls, entity: UsageRecordEntity) -> "UsageRecord":
        usage_kind = (
            entity.usage_kind.value
            if hasattr(entity.usage_kind, "value")
            else str(entity.usage_kind)
        )
        profile_scope = (
            entity.profile_scope.value
            if hasattr(entity.profile_scope, "value")
            else str(entity.profile_scope)
        )
        return cls(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            organization_id=entity.organization_id,
            pod_id=entity.pod_id,
            user_id=entity.user_id,
            agent_id=entity.agent_id,
            conversation_id=entity.conversation_id,
            agent_run_id=entity.agent_run_id,
            parent_agent_run_id=entity.parent_agent_run_id,
            source_type=entity.source_type,
            source_id=entity.source_id,
            profile_id=entity.profile_id,
            profile_scope=profile_scope,
            model_name=entity.model_name,
            usage_kind=usage_kind,
            input_tokens=entity.input_tokens,
            output_tokens=entity.output_tokens,
            units=entity.units,
            cost_usd=entity.cost_usd,
            status=entity.status,
            record_metadata=entity.metadata,
            occurred_at=entity.occurred_at,
        )


class UsageLimitCounter(UUIDAuditBase):
    """Reserved/used system-profile cost for one active limit window."""

    __tablename__ = "usage_limit_counters"

    organization_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    window_kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reserved_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index(
            "uq_usage_limit_counter_window",
            "organization_id",
            "user_id",
            "window_kind",
            "window_start",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
