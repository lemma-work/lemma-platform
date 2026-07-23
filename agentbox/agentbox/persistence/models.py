from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agentbox.domain import utc_now


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class LogicalSandboxRow(TimestampMixin, Base):
    __tablename__ = "sandboxes"
    __table_args__ = (
        CheckConstraint(
            "workload_kind IN ('workspace', 'function')", name="workload_kind"
        ),
        CheckConstraint(
            "desired_state IN ('present', 'released', 'deleted')",
            name="desired_state",
        ),
        CheckConstraint(
            "maintenance_action IS NULL OR "
            "maintenance_action IN ('release', 'destroy')",
            name="maintenance_action",
        ),
        Index(
            "ix_sandboxes_cleanup",
            "desired_state",
            "last_used_at",
            "delete_after",
            "maintenance_claimed_until",
        ),
    )

    workload_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    logical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    desired_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="present"
    )
    profile_name: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    current_allocation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    allocation_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    protected_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delete_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    maintenance_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    maintenance_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, unique=True
    )
    maintenance_claimed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkspaceStorageRow(TimestampMixin, Base):
    __tablename__ = "workspace_storage"
    __table_args__ = (
        CheckConstraint("workload_kind = 'workspace'", name="workspace_only"),
        CheckConstraint(
            "storage_kind IN ('volume', 'pvc', 'sandbox_native')",
            name="storage_kind",
        ),
        CheckConstraint(
            "state IN ('provisioning', 'ready', 'migrating', 'deleting', "
            "'deleted', 'error')",
            name="storage_state",
        ),
        UniqueConstraint(
            "provider_name", "provider_storage_id", name="storage_provider_id"
        ),
    )

    workload_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    logical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_storage_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    bound_allocation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="provisioning"
    )
    content_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    delete_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, unique=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AllocationRow(TimestampMixin, Base):
    __tablename__ = "allocations"
    __table_args__ = (
        CheckConstraint(
            "workload_kind IN ('workspace', 'function')", name="workload_kind"
        ),
        CheckConstraint(
            "state IN ('reserved', 'provisioning', 'unknown', 'active', "
            "'quiescing', 'released', 'draining', 'destroying', 'destroyed', "
            "'error')",
            name="allocation_state",
        ),
        CheckConstraint(
            "admission_state IN ('unreserved', 'reserved', 'active', 'released')",
            name="admission_state",
        ),
        UniqueConstraint(
            "provider_scope", "provider_id", name="allocation_provider_id"
        ),
        Index(
            "ix_allocations_owner",
            "workload_kind",
            "logical_id",
            "state",
        ),
    )

    allocation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workload_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    logical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    allocation_token: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, unique=True
    )
    provider_name: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_scope: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_instance_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    profile_name: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    admission_class: Mapped[str] = mapped_column(String(32), nullable=False)
    admission_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unreserved"
    )
    allocation_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CreateAttemptRow(TimestampMixin, Base):
    __tablename__ = "allocation_create_attempts"
    __table_args__ = (
        CheckConstraint(
            "dispatch_state IN ('reserved', 'dispatched', 'acknowledged', "
            "'unknown', 'resolved')",
            name="dispatch_state",
        ),
        Index("ix_allocation_create_reconcile", "dispatch_state", "reconcile_after"),
    )

    allocation_token: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("allocations.allocation_token", ondelete="CASCADE"),
        primary_key=True,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dispatch_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reserved"
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    last_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessIntentRow(TimestampMixin, Base):
    __tablename__ = "processes"
    __table_args__ = (
        CheckConstraint(
            "workload_kind IN ('workspace', 'function')", name="workload_kind"
        ),
        CheckConstraint(
            "state IN ('reserved', 'starting', 'unknown', 'running', "
            "'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="process_state",
        ),
        Index("ix_processes_allocation", "allocation_id", "state"),
    )

    workload_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    logical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("allocations.allocation_id", ondelete="CASCADE"),
        nullable=False,
    )
    allocation_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    env_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cwd: Mapped[str] = mapped_column(String(4096), nullable=False)
    tty: Mapped[bool] = mapped_column(nullable=False, default=False)
    output_limit_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_process_id: Mapped[str | None] = mapped_column(String(256))
    provider_tag: Mapped[str | None] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output_tail: Mapped[str | None] = mapped_column(Text)
    truncated_before_seq: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionRow(TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("workload_kind = 'workspace'", name="workspace_only"),
        CheckConstraint(
            "state IN ('reserved', 'creating', 'unknown', 'active', 'stale', "
            "'deleted')",
            name="session_state",
        ),
        Index("ix_sessions_allocation", "allocation_id", "state"),
    )

    workload_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    logical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("allocations.allocation_id", ondelete="CASCADE"),
        nullable=False,
    )
    allocation_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_context_id: Mapped[str | None] = mapped_column(String(256))
    cwd: Mapped[str] = mapped_column(String(4096), nullable=False)
    env_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PythonExecutionRow(TimestampMixin, Base):
    __tablename__ = "python_executions"
    __table_args__ = (
        CheckConstraint("workload_kind = 'workspace'", name="workspace_only"),
        CheckConstraint(
            "state IN ('reserved', 'starting', 'unknown', 'succeeded', 'failed', "
            "'timed_out')",
            name="python_execution_state",
        ),
        Index("ix_python_executions_session", "session_id", "created_at"),
    )

    workload_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    logical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    allocation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("allocations.allocation_id", ondelete="CASCADE"),
        nullable=False,
    )
    allocation_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[str | None] = mapped_column(Text)
    error_name: Mapped[str | None] = mapped_column(String(256))
    error_message: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    output_truncated: Mapped[bool] = mapped_column(nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderAdmissionRow(TimestampMixin, Base):
    __tablename__ = "provider_admission"

    provider_scope: Mapped[str] = mapped_column(String(256), primary_key=True)
    max_active: Mapped[int] = mapped_column(Integer, nullable=False)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_tokens: Mapped[float] = mapped_column(nullable=False, default=0.0)
    create_rate_per_second: Mapped[float] = mapped_column(nullable=False, default=1.0)
    create_burst: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    token_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interactive_capacity_reserve: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    latency_capacity_reserve: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
