"""Function database models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionAttemptStatus,
    FunctionExecutionStatus,
    FunctionRevisionEntity,
    FunctionRevisionStatus,
    FunctionRunEntity,
    FunctionStatus,
    FunctionRunStatus,
    FunctionType,
)


class FunctionModel(UUIDAuditBase):
    """Database model for functions."""

    __tablename__ = "functions"
    __table_args__ = (
        UniqueConstraint("pod_id", "name", name="uq_function_pod_name"),
        Index("ix_function_name", "name"),
        Index("ix_function_pod_name", "pod_id", "name"),
    )

    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    config_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    code_path: Mapped[str | None] = mapped_column(String, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    type: Mapped[FunctionType] = mapped_column(String, default=FunctionType.API)
    status: Mapped[FunctionStatus] = mapped_column(String, default=FunctionStatus.DRAFT)
    visibility: Mapped[str] = mapped_column(String(30), default="POD", nullable=False)
    python_packages: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )
    active_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)

    def __str__(self) -> str:
        return self.name or str(self.id)

    # Relationships
    runs: Mapped[list["FunctionRunModel"]] = relationship(
        "FunctionRunModel",
        back_populates="function",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    revisions: Mapped[list["FunctionRevisionModel"]] = relationship(
        "FunctionRevisionModel",
        back_populates="function",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def to_entity(self) -> FunctionEntity:
        entity_data = self.__dict__.copy()
        return FunctionEntity.model_validate(entity_data)


class FunctionRevisionModel(UUIDAuditBase):
    """Immutable, content-addressed executable revision."""

    __tablename__ = "function_revisions"
    __table_args__ = (
        UniqueConstraint(
            "function_id", "revision_number", name="uq_function_revision_number"
        ),
        UniqueConstraint(
            "function_id", "artifact_sha256", name="uq_function_revision_artifact"
        ),
        Index("ix_function_revision_ready", "function_id", "status"),
    )

    function_id: Mapped[UUID] = mapped_column(
        ForeignKey("functions.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[FunctionRevisionStatus] = mapped_column(String(16), nullable=False)
    code_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    runtime_abi: Mapped[str] = mapped_column(String(128), nullable=False)
    builder_digest: Mapped[str] = mapped_column(String(256), nullable=False)
    dependency_lock: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotent: Mapped[bool] = mapped_column(nullable=False, default=False)

    function: Mapped["FunctionModel"] = relationship(
        "FunctionModel", back_populates="revisions", lazy="raise"
    )

    def to_entity(self) -> FunctionRevisionEntity:
        return FunctionRevisionEntity.model_validate(self)


class FunctionRunModel(UUIDAuditBase):
    """Database model for function runs."""

    __tablename__ = "function_runs"

    function_id: Mapped[UUID] = mapped_column(
        ForeignKey("functions.id", ondelete="CASCADE")
    )
    revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("function_revisions.id"), nullable=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    input_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[FunctionRunStatus] = mapped_column(
        String, default=FunctionRunStatus.PENDING
    )
    user_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_process_id: Mapped[str | None] = mapped_column(String, nullable=True)
    current_attempt_id: Mapped[UUID | None] = mapped_column(nullable=True)
    execution_fence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    function: Mapped["FunctionModel"] = relationship(
        "FunctionModel", back_populates="runs", lazy="selectin"
    )

    def to_entity(self) -> FunctionRunEntity:
        return FunctionRunEntity.model_validate(self)


class FunctionExecutionRequestModel(UUIDAuditBase):
    """One durable queue entry per public function run."""

    __tablename__ = "function_execution_requests"
    __table_args__ = (
        Index(
            "ix_function_execution_queue",
            "status",
            "priority",
            "available_at",
            "created_at",
        ),
        Index("ix_function_execution_capacity", "pod_id", "status", "kind"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("function_runs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    function_id: Mapped[UUID] = mapped_column(
        ForeignKey("functions.id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("function_revisions.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FunctionExecutionStatus.QUEUED.value
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    next_fence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FunctionExecutionAttemptModel(UUIDAuditBase):
    """Fenced physical attempt mapped to one AgentBox process operation."""

    __tablename__ = "function_execution_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "number", name="uq_function_attempt_number"),
        UniqueConstraint("run_id", "fence", name="uq_function_attempt_fence"),
        Index("ix_function_attempt_ticket", "ticket_digest"),
        Index("ix_function_attempt_runtime", "runtime_token_digest"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("function_runs.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("function_execution_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=FunctionAttemptStatus.RESERVED.value
    )
    ticket_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    runtime_token_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    ticket_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ticket_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_process_id: Mapped[str | None] = mapped_column(String(256))
    terminal_payload_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
