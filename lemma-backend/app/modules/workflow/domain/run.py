"""WorkflowRun aggregate: the run state machine.

The stepping loop lives in execution/stepper.py — this entity owns state
transitions, step history, and the typed run context.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.domain.aggregate import AggregateRoot
from app.modules.workflow.domain.context import (
    RunContext,
    TriggerContext,
    normalize_node_output,
)
# Keep stored failure reasons actionable, not full tracebacks. We retain the
# head (usually the human-readable message) and the tail (usually the root
# cause line of a chained exception) so both survive truncation.
MAX_ERROR_LENGTH = 2000


def summarize_error(error: str | None) -> str | None:
    """Bound a failure reason to an actionable, storable size."""
    if error is None:
        return None
    text = " ".join(str(error).split())
    if len(text) <= MAX_ERROR_LENGTH:
        return text
    head = text[: MAX_ERROR_LENGTH - 600]
    tail = text[-500:]
    return f"{head} … [truncated] … {tail}"


class WorkflowRunStatus(str, Enum):
    """Status of a flow run.

    PENDING exists only in memory before the first advance; persisted runs
    are RUNNING, WAITING, or terminal. WAITING is reserved for human form
    waits. Runs suspended on platform work such as an agent, function job, or
    timer remain RUNNING; the active wait row records the exact wait_type.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.CANCELLED,
}


class StepStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LoopFrame(BaseModel):
    """Execution stack frame for a loop in progress."""

    loop_node_id: str
    body_node_id: str
    index: int = 0
    items: List[Any] = Field(default_factory=list)
    item_var: str = "item"
    results: List[Any] = Field(default_factory=list)


class StepRecord(BaseModel):
    """Record of a single node execution."""

    step_index: int
    node_id: str
    status: StepStatus
    started_at: datetime
    completed_at: datetime | None = None
    output_data: Any | None = None
    error: str | None = None


class WorkflowRunEntity(AggregateRoot):
    """WorkflowRun aggregate representing an execution of a flow."""

    flow_id: UUID
    pod_id: UUID
    user_id: UUID
    start_type: str = "MANUAL"
    schedule_event_id: str | None = None
    # Raw trigger payload for audit/debugging; the canonical view is
    # execution_context.start.
    start_payload: Dict[str, Any] = Field(default_factory=dict)

    status: WorkflowRunStatus = WorkflowRunStatus.PENDING

    current_node_id: str | None = None
    execution_stack: List[LoopFrame] = Field(default_factory=list)
    execution_context: RunContext = Field(default_factory=RunContext)
    step_history: List[StepRecord] = Field(default_factory=list)

    # Failure surface (populated only when status == FAILED)
    error: str | None = Field(
        default=None,
        description="Human-readable reason the run failed. Truncated to stay actionable.",
    )
    failed_node_id: str | None = Field(
        default=None,
        description="Id of the node that failed, when the failure is scoped to a node.",
    )

    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        flow_id: UUID,
        pod_id: UUID,
        user_id: UUID,
        entry_node_id: str,
        trigger: TriggerContext | None = None,
        schedule_event_id: str | None = None,
    ) -> "WorkflowRunEntity":
        """Create a run positioned at the entry node.

        start_type comes from the trigger (the call site is the source of
        truth), never from the flow's declared start config.
        """
        run = cls(
            flow_id=flow_id,
            pod_id=pod_id,
            user_id=user_id,
            start_type=trigger.trigger_type.value if trigger else "MANUAL",
            schedule_event_id=schedule_event_id,
            start_payload=trigger.to_context_value() if trigger else {},
            status=WorkflowRunStatus.RUNNING,
            current_node_id=entry_node_id,
            started_at=datetime.now(timezone.utc),
        )
        if trigger is not None:
            run.execution_context.set_start(trigger)
        return run

    # -- stepping hooks (called by the stepper) -------------------------------

    def begin_step(self, node_id: str) -> StepRecord:
        step = StepRecord(
            step_index=len(self.step_history),
            node_id=node_id,
            status=StepStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self.step_history.append(step)
        return step

    def complete_step(self, step: StepRecord, output: Any = None) -> None:
        step.status = StepStatus.COMPLETED
        step.completed_at = datetime.now(timezone.utc)
        step.output_data = output

    def suspend_step(
        self,
        step: StepRecord,
        output: Any = None,
        *,
        human_wait: bool = False,
    ) -> None:
        step.output_data = output
        if human_wait:
            step.status = StepStatus.WAITING
            self.status = WorkflowRunStatus.WAITING

    def record_node_output(self, node_id: str, output: Any) -> dict[str, Any]:
        normalized = normalize_node_output(output)
        self.execution_context.record_node_output(node_id, normalized)
        return normalized

    # -- transitions -----------------------------------------------------------

    def resume(self, node_id: str, output: Dict[str, Any]) -> None:
        """Complete the suspend on node_id with its output and return to RUNNING."""
        if self.status not in (WorkflowRunStatus.WAITING, WorkflowRunStatus.RUNNING):
            raise ValueError(f"Cannot resume flow run in {self.status.value} state")
        if self.current_node_id != node_id:
            raise ValueError(
                f"Run is suspended on node '{self.current_node_id}', not '{node_id}'"
            )
        last_step = self._last_suspended_step(node_id)
        if last_step is None:
            raise ValueError(
                f"Cannot resume flow run; node '{node_id}' is not suspended"
            )
        normalized = self.record_node_output(node_id, output)
        last_step.status = StepStatus.COMPLETED
        last_step.completed_at = datetime.now(timezone.utc)
        last_step.output_data = normalized
        self.status = WorkflowRunStatus.RUNNING

    def complete(self) -> None:
        self.status = WorkflowRunStatus.COMPLETED
        self.current_node_id = None
        self.completed_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        if self.status in TERMINAL_STATUSES:
            raise ValueError(f"Cannot cancel flow run in {self.status.value} state")
        waiting_step = (
            self._last_suspended_step(self.current_node_id)
            if self.current_node_id
            else None
        )
        if waiting_step is not None:
            waiting_step.status = StepStatus.CANCELLED
            waiting_step.completed_at = datetime.now(timezone.utc)
        self.status = WorkflowRunStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)

    def fail(self, error: str, *, node_id: str | None = None) -> None:
        """Every failure path funnels through here so the reason survives to
        the API. failed_node_id is the node being executed when the failure
        happened (None for pre-execution failures)."""
        failed_node = node_id or self.current_node_id
        if self.step_history:
            last_step = self.step_history[-1]
            if last_step.status in (StepStatus.RUNNING, StepStatus.WAITING):
                last_step.status = StepStatus.FAILED
                last_step.completed_at = datetime.now(timezone.utc)
                last_step.error = summarize_error(error)
        self.status = WorkflowRunStatus.FAILED
        self.error = summarize_error(error)
        self.failed_node_id = failed_node
        self.completed_at = datetime.now(timezone.utc)

    def _last_suspended_step(self, node_id: str | None) -> StepRecord | None:
        for step in reversed(self.step_history):
            if step.status in (StepStatus.WAITING, StepStatus.RUNNING) and (
                node_id is None or step.node_id == node_id
            ):
                return step
        return None
