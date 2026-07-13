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


class WorkflowRunTerminalEvent(WorkflowRunEvent):
    event_type: str = "workflow.run.terminal"
    run_id: UUID
    status: WorkflowRunStatus
    error: str | None = None
    completed_at: datetime
