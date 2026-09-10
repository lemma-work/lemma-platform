"""What `workflow` needs from a person's inbox.

One factory and one operation, replacing `app/composition/workflow_notifications.py`.
That file said it lived outside both modules because "the workflow module must
not import `agent_surfaces`"; `workflow` now reaches `agent_surfaces` through
published contracts like anything else, and the adapter it held is made entirely
of this module's service, so this is where it belongs.

The factory is the same exception to "operations, not classes" as
`agent/contracts/workflow_control.py`: `WorkflowNotificationPort` is a port the
engine holds for the length of a run, and publishing its three methods as free
functions would only make `build_workflow_engine` reassemble them. The
implementation stays unpublished -- the caller names the port and cannot reach
past it to the notification service.

`expire_past_due` is here rather than beside the other notification operations
because `workflow`'s cron is its only caller, and it is the sweep that closes
out the notifications this port opened.

A submodule rather than `contracts/__init__`: this reaches the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.api.dependencies import get_notification_service
from app.modules.agent_surfaces.infrastructure.adapters.workflow_notifications import (
    WorkflowNotificationAdapter,
)
from app.modules.workflow.contracts import WorkflowNotificationPort


def build_workflow_notification_adapter(
    uow: SqlAlchemyUnitOfWork,
) -> WorkflowNotificationPort:
    """The inbox a run tells about its human waits, bound to this transaction."""
    return WorkflowNotificationAdapter(uow)


async def expire_past_due_notifications(uow: SqlAlchemyUnitOfWork) -> int:
    """Close out notifications nobody answered before their deadline.

    Not a failure — people are busy, and the deadline is 72h precisely so that
    ordinary out-of-hours delay does not trip it. But a row that stays OPEN
    forever is an inbox badge that never clears and an asking run waiting on
    something that will never arrive.

    Returns how many were closed, for the cron that logs it. The caller commits:
    it holds the unit of work, and the sweep runs beside two others in the same
    transaction.
    """
    return await get_notification_service(uow).expire_past_due()


__all__ = ["build_workflow_notification_adapter", "expire_past_due_notifications"]
