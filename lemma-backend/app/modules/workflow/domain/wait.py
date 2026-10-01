"""Workflow run waits: the single source of truth for what a run waits on."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.domain.aggregate import AggregateRoot


class WorkflowRunWaitType(str, Enum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"
    TIME = "TIME"
    #: A DECISION node's question, asked by a job outside the run transaction.
    DECISION = "DECISION"


class WorkflowRunWaitStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WaitRequest(BaseModel):
    """Explicit wait description returned by a suspending executor.

    External refs (agent conversation id, function run id, timer id) live
    here and on the wait row — never in the run context.
    """

    wait_type: WorkflowRunWaitType
    external_ref: str | None = None
    assigned_pod_member_id: UUID | None = None
    scheduled_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunWaitEntity(AggregateRoot):
    """A queryable wait owned by a workflow run."""

    run_id: UUID
    flow_id: UUID
    pod_id: UUID
    node_id: str
    wait_type: WorkflowRunWaitType
    status: WorkflowRunWaitStatus = WorkflowRunWaitStatus.ACTIVE

    assigned_pod_member_id: UUID | None = None
    external_ref: str | None = None
    scheduled_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime | None = None

    @classmethod
    def for_request(
        cls,
        request: WaitRequest,
        *,
        run_id: UUID,
        flow_id: UUID,
        pod_id: UUID,
        node_id: str,
    ) -> "WorkflowRunWaitEntity":
        """The row a step suspending with `request` waits on."""
        payload = dict(request.payload)
        if request.scheduled_at is not None:
            payload.setdefault("scheduled_at", request.scheduled_at.isoformat())
        return cls(
            run_id=run_id,
            flow_id=flow_id,
            pod_id=pod_id,
            node_id=node_id,
            wait_type=request.wait_type,
            assigned_pod_member_id=request.assigned_pod_member_id,
            external_ref=request.external_ref,
            # Kept in `payload` too: the reconcile sweep still reads it from
            # there, and older rows have only that copy.
            scheduled_at=request.scheduled_at,
            payload=payload,
        )

    def complete(self, payload: dict[str, Any] | None = None) -> None:
        self.status = WorkflowRunWaitStatus.COMPLETED
        self.payload = payload or self.payload
        self.completed_at = datetime.now(timezone.utc)

    def fail(self, payload: dict[str, Any] | None = None) -> None:
        self.status = WorkflowRunWaitStatus.FAILED
        self.payload = payload or self.payload
        self.completed_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        self.status = WorkflowRunWaitStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)
