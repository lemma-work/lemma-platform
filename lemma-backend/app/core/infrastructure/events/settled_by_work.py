"""Old inbox consumers whose work this release does through the work runtime.

This release moved several consumer groups off the event transport: the work
each one did is now written as a work item (or a fact's items) in the very
transaction that raises the event. The events themselves still go to the
outbox -- other groups (analytics, agents, surfaces) still read them -- but
nothing will ever consume them under the moved groups' inbox names again.

The next release converts whatever the old transport still owes into work
items (``cutover.legacy_event``): an event is owed to an old consumer when that
consumer's inbox holds no settled row for it. So the unit of work marks the
moved consumers as settled for every event it writes, in the same transaction:
``COMPLETED`` with ``last_error_type = 'work_runtime'``. Without the mark,
the conversion could not tell an event this release already handled from one
written by the previous release whose consumer never ran, and would do the
work twice.

Strings only: the consumer names are the old ``inbox.process`` names, which
outlive the code that used them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.core.domain.events import DomainEvent

#: Written into ``last_error_type`` of a settled-by-work inbox row.
SETTLED_BY_WORK_MARK = "work_runtime"

#: event type -> the moved consumers that would have handled it.
SETTLED_BY_WORK: dict[str, tuple[str, ...]] = {
    "pod.join_request.requested": ("pod.join-request-email",),
    "pod.created": ("datastore.pod-schema-bootstrap",),
    "pod.deleted": (
        "schedule-pod-events",
        "agent-surfaces.pod-deletion",
        "agent.pod-links.pod-deletion",
        "mcp-access.pod-deletion",
    ),
    "pod.member.removed": ("agent-surfaces.pod-member-removed",),
    "schedule.deactivated": ("schedule-notifications",),
    "workflow.run.terminal": ("schedule.workflow-outcomes",),
    "identity.user.signed_up": ("identity-email-events",),
    "identity.user.phone.replaced": ("identity-email-events",),
    "identity.organization.invitation.created": ("identity-email-events",),
    "identity.organization.invitation.accepted": ("identity-email-events",),
    "identity.mobile_verification.whatsapp.received": (
        "identity-mobile-verification-events",
    ),
}


def settled_inbox_rows(events: Sequence[DomainEvent]) -> list[dict[str, object]]:
    """The inbox rows that mark ``events`` settled for their moved consumers."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": uuid4(),
            "consumer": consumer,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "status": "COMPLETED",
            "attempts": 0,
            "delivery_count": 0,
            "first_received_at": now,
            "last_received_at": now,
            "completed_at": now,
            "last_error_type": SETTLED_BY_WORK_MARK,
            "created_at": now,
        }
        for event in events
        for consumer in SETTLED_BY_WORK.get(event.event_type, ())
    ]


__all__ = ["SETTLED_BY_WORK", "SETTLED_BY_WORK_MARK", "settled_inbox_rows"]
