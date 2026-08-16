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
