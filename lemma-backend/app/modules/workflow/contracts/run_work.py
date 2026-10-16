"""Starting a workflow run as work, and what a run announces when it ends.

``enqueue_workflow_start`` writes ``workflow.start`` in the caller's
transaction -- a schedule's fire writes it with the ``schedule_runs`` row it
claims -- so the start cannot be lost between the two. The handler starts the
run under the reserved ``run_id``, which makes a repeated item return the run
it already started instead of starting a second.

``WorkflowRunFinished`` is emitted in the transaction that makes a run
terminal; ``WorkflowStartRefused`` when a start item could not create its run
at all (the workflow was deleted, its owner lost access). Subscribe a work kind
to either with ``facts.subscribe``.

A submodule rather than ``contracts/__init__``: that one imports the API
schemas, and a FAST handler in another module that writes a start should not
drag them into its process for it.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import Fact, WorkWriter, pod_namespace

WORKFLOW_START = "workflow.start"

WorkflowTriggerType = Literal["SCHEDULED", "EVENT", "DATASTORE_EVENT"]
WorkflowRunOutcome = Literal["COMPLETED", "FAILED", "CANCELLED"]


class WorkflowTrigger(BaseModel):
    """What the run reads as ``start.*``: the event that started it."""

    trigger_type: WorkflowTriggerType = "SCHEDULED"
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    llm_output: dict[str, JsonValue] = Field(default_factory=dict)


class WorkflowStartRequest(BaseModel):
    """One of two shapes.

    * ``schedule_run_id`` alone -- a schedule's reserved run. The workflow
      module reads the fire back from it (``schedule.contracts.reserved_runs``)
      and starts the target the way a schedule fire always has: claim the run,
      build ``start.*`` from the schedule, mark it dispatched.
    * ``flow_id`` + ``user_id`` + ``run_id`` -- a run any other module asks for.
    """

    schedule_run_id: UUID | None = None
    flow_id: UUID | None = None
    #: Whose run it is; the start is authorized as them when it runs.
    user_id: UUID | None = None
    #: Reserved by the caller: the start is idempotent on it.
    run_id: UUID | None = None
    #: The source's event id, recorded on the run (unique per flow and user).
    schedule_event_id: str | None = None
    trigger: WorkflowTrigger | None = None

    @model_validator(mode="after")
    def _names_a_start(self) -> WorkflowStartRequest:
        direct = (self.flow_id, self.user_id, self.run_id)
        if self.schedule_run_id is None and any(part is None for part in direct):
            raise ValueError(
                "a workflow start names a schedule_run_id, or a flow_id, user_id "
                "and run_id"
            )
        return self


async def enqueue_workflow_start(
    session: AsyncSession, *, pod_id: UUID, request: WorkflowStartRequest
) -> None:
    """Write ``workflow.start`` in ``session``'s transaction."""
    # The kind is registered by the module that runs it; importing it here
    # means a caller never depends on assembly order to enqueue.
    import app.modules.workflow.events.run_work  # noqa: F401

    key = (
        f"schedule-run:{request.schedule_run_id}"
        if request.schedule_run_id is not None
        else str(request.run_id)
    )
    await WorkWriter(session).add(
        WORKFLOW_START,
        pod_namespace(pod_id),
        request,
        dedup_key=f"workflow-start:{key}",
    )


class WorkflowRunFinished(Fact):
    run_id: UUID
    pod_id: UUID
    flow_id: UUID
    status: WorkflowRunOutcome
    schedule_event_id: str | None = None
    error: str | None = None

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


class WorkflowStartRefused(Fact):
    pod_id: UUID
    error: str
    schedule_run_id: UUID | None = None
    run_id: UUID | None = None
    flow_id: UUID | None = None
    schedule_event_id: str | None = None

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = [
    "WORKFLOW_START",
    "WorkflowRunFinished",
    "WorkflowRunOutcome",
    "WorkflowStartRefused",
    "WorkflowStartRequest",
    "WorkflowTrigger",
    "WorkflowTriggerType",
    "enqueue_workflow_start",
]
