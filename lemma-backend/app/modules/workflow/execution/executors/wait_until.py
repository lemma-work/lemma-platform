"""Wait-until node executor: suspend on a TIME wait and set its timer.

The timer is a ``workflow.resume`` item due at ``scheduled_at``, written in the
same transaction as the wait -- the wait row cannot exist without the work that
wakes it.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.modules.workflow.domain.nodes import WaitUntilNode
from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitType
from app.modules.workflow.execution.outcome import NodeOutcome, Suspend
from app.modules.workflow.execution.step_context import StepContext
from app.modules.workflow.execution.wait_work import write_timer


class WaitUntilExecutor:
    async def execute(self, node: WaitUntilNode, step: StepContext) -> NodeOutcome:
        scheduled_at = datetime.now(timezone.utc) + timedelta(
            seconds=node.config.timeout_seconds
        )
        # One token per wait, so sequential timers on one run never wake each
        # other's wait.
        timer_ref = str(uuid4())
        await write_timer(
            step.work, pod_id=step.pod_id, external_ref=timer_ref, fire_at=scheduled_at
        )
        return Suspend(
            wait=WaitRequest(
                wait_type=WorkflowRunWaitType.TIME,
                external_ref=timer_ref,
                scheduled_at=scheduled_at,
                payload={"scheduled_at": scheduled_at.isoformat()},
            )
        )
