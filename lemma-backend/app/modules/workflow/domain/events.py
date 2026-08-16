"""Workflow execution lifecycle events."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from app.core.domain.events import DomainEvent
from app.modules.workflow.domain.run import WorkflowRunStatus

WORKFLOW_RUN_EVENTS_STREAM = "workflow_run_events"


class WorkflowRunEvent(DomainEvent):
    _stream_name: ClassVar[str] = WORKFLOW_RUN_EVENTS_STREAM

    @classmethod
    def stream_name(cls) -> str:
        return cls._stream_name


class WorkflowCreatedEvent(WorkflowRunEvent):
    """A workflow was defined.

    On the run stream rather than a stream of its own: this is the only
    non-run event the module raises, and analytics already subscribes here for
    terminal runs. One consumer group beats two for one event.
    """

    event_type: str = "workflow.created"
    workflow_id: UUID
    pod_id: UUID
    user_id: UUID | None = None
    node_count: int = 0


class WorkflowRunTerminalEvent(WorkflowRunEvent):
    event_type: str = "workflow.run.terminal"
    run_id: UUID
    status: WorkflowRunStatus
    error: str | None = None
    completed_at: datetime
    #: Optional with defaults, deliberately: during a rolling deploy an older
    #: replica is still publishing events without these, and the existing
    #: consumer at `schedule_target_outcome_consumer` must keep validating them.
    #: Populated by the engine, which holds the run entity anyway -- reading the
    #: row again in the consumer would be a query per terminal run for data the
    #: producer already had.
    workflow_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID | None = None
    started_at: datetime | None = None

    @classmethod
    def from_run(cls, run) -> "WorkflowRunTerminalEvent":
        """Build from the run entity, which the engine already holds.

        Here rather than in the engine because the engine is at its size ceiling
        and this is the event's own business anyway: it knows which of the run's
        fields it carries.
        """
        if run.completed_at is None:
            raise RuntimeError(f"Terminal workflow run {run.id} has no completed_at")
        return cls(
            run_id=run.id,
            status=run.status,
            error=run.error,
            completed_at=run.completed_at,
            workflow_id=run.flow_id,
            pod_id=run.pod_id,
            user_id=run.user_id,
            started_at=run.started_at,
        )
