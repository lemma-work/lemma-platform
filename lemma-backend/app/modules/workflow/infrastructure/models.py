"""SQLAlchemy models for Workflow module."""

from __future__ import annotations

from uuid import UUID
from datetime import datetime

from sqlalchemy import (
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
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.workflow.domain.nodes import WORKFLOW_NODE_ADAPTER


class WorkflowModel(UUIDAuditBase):
    """Workflow definitions table."""

    __tablename__ = "workflow_flows"
    __table_args__ = (
        UniqueConstraint("pod_id", "name", name="uq_workflow_flow_pod_name"),
        Index("ix_workflow_flow_pod_name", "pod_id", "name"),
    )

    # Base provides id, created_at, updated_at

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Store nodes and edges as JSON blobs for simplicity and flexibility
    nodes: Mapped[list] = mapped_column(JSONB, default=list)
    edges: Mapped[list] = mapped_column(JSONB, default=list)
    # Computed by graph validation at save time; null for graph-less shells.
    entry_node_id: Mapped[str | None] = mapped_column(String, nullable=True)

    start: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)

    mode: Mapped[str] = mapped_column(String, default="GLOBAL", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    visibility: Mapped[str] = mapped_column(String(30), default="POD", nullable=False)

    runs: Mapped[list["WorkflowRunModel"]] = relationship(
        "WorkflowRunModel", back_populates="flow", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:
        return self.name or str(self.id)

    def to_entity(self):
        from app.modules.workflow.domain.workflow import WorkflowEntity
        from app.modules.workflow.domain.graph import WorkflowEdge
        from app.modules.workflow.domain.start import WorkflowStart

        nodes = [WORKFLOW_NODE_ADAPTER.validate_python(n) for n in self.nodes]
        edges = [WorkflowEdge(**e) for e in self.edges]
        return WorkflowEntity(
            id=self.id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            name=self.name,
            description=self.description,
            icon_url=self.icon_url,
            nodes=nodes,
            edges=edges,
            entry_node_id=self.entry_node_id,
            start=WorkflowStart(**self.start) if self.start else None,
            mode=self.mode,
            is_active=self.is_active,
            visibility=self.visibility,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class WorkflowRunModel(UUIDAuditBase):
    """Workflow execution instances table.

    External wait refs live exclusively on workflow_run_waits — there are no
    waiting_* columns here.
    """

    __tablename__ = "workflow_flow_runs"
    __table_args__ = (
        UniqueConstraint(
            "flow_id",
            "user_id",
            "schedule_event_id",
            name="uq_workflow_run_flow_user_schedule_event",
        ),
    )

    # Base provides id, created_at, updated_at

    flow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_flows.id", ondelete="CASCADE"), index=True
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    start_type: Mapped[str] = mapped_column(String, nullable=False, default="MANUAL")
    schedule_event_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    start_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")

    current_node_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Execution state
    execution_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    execution_stack: Mapped[list] = mapped_column(JSONB, default=list)
    step_history: Mapped[list] = mapped_column(JSONB, default=list)

    # Failure surface (populated only when status == FAILED)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_node_id: Mapped[str | None] = mapped_column(String, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    flow: Mapped["WorkflowModel"] = relationship("WorkflowModel", back_populates="runs")

    def to_entity(self):
        from app.modules.workflow.domain.context import RunContext
        from app.modules.workflow.domain.run import (
            WorkflowRunEntity,
            WorkflowRunStatus,
            LoopFrame,
            StepRecord,
        )

        step_history = (
            [StepRecord(**s) for s in self.step_history] if self.step_history else []
        )
        execution_stack = (
            [LoopFrame(**f) for f in self.execution_stack]
            if self.execution_stack
            else []
        )
        return WorkflowRunEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            flow_id=self.flow_id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            start_type=self.start_type,
            schedule_event_id=self.schedule_event_id,
            start_payload=self.start_payload or {},
            status=WorkflowRunStatus(self.status),
            current_node_id=self.current_node_id,
            execution_context=RunContext.model_validate(self.execution_context or {}),
            execution_stack=execution_stack,
            step_history=step_history,
            error=self.error,
            failed_node_id=self.failed_node_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )


class WorkflowRunWaitModel(UUIDAuditBase):
    """Queryable waits for workflow runs — the single source of truth for
    what a run is waiting on (type, external ref, assignee)."""

    __tablename__ = "workflow_run_waits"
    __table_args__ = (
        Index("ix_workflow_run_waits_run_status", "run_id", "status"),
        Index(
            "ix_workflow_run_waits_assignee_status",
            "assigned_pod_member_id",
            "status",
        ),
        Index("ix_workflow_run_waits_external_ref", "external_ref"),
        Index(
            "ix_workflow_run_waits_type_ref_status",
            "wait_type",
            "external_ref",
            "status",
        ),
        # DB-enforced invariant: at most one ACTIVE wait per run.
        Index(
            "uq_workflow_run_waits_one_active",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_flow_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    flow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_flows.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    wait_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    assigned_pod_member_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pod_members.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_entity(self):
        from app.modules.workflow.domain.wait import (
            WorkflowRunWaitEntity,
            WorkflowRunWaitStatus,
            WorkflowRunWaitType,
        )

        return WorkflowRunWaitEntity(
            id=self.id,
            run_id=self.run_id,
            flow_id=self.flow_id,
            pod_id=self.pod_id,
            node_id=self.node_id,
            wait_type=WorkflowRunWaitType(self.wait_type),
            status=WorkflowRunWaitStatus(self.status),
            assigned_pod_member_id=self.assigned_pod_member_id,
            external_ref=self.external_ref,
            payload=self.payload or {},
            completed_at=self.completed_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
