from datetime import datetime
from typing import Any
from uuid import UUID
from app.core.domain.events import DomainEvent
from app.modules.schedule.domain.schedule import ScheduleType


class ScheduleEvent(DomainEvent):
    schedule_id: UUID
    user_id: UUID
    schedule_type: ScheduleType

    @classmethod
    def stream_name(cls) -> str:
        return "schedule_events"


class ScheduleCreated(ScheduleEvent):
    event_type: str = "schedule.created"
    config: dict[str, Any]


class ScheduleUpdated(ScheduleEvent):
    event_type: str = "schedule.updated"
    config: dict[str, Any]


class ScheduleDeleted(ScheduleEvent):
    event_type: str = "schedule.deleted"


class ScheduleDeactivated(ScheduleEvent):
    """A schedule was auto-deactivated by the failure circuit breaker.

    Emitted when a schedule hits the consecutive-failure threshold and is set
    inactive. Consumed today to notify the creator; the event is the extension
    point for future reactions (in-app notification, admin alerting) without
    changing the breaker.
    """

    event_type: str = "schedule.deactivated"
    consecutive_failures: int
    reason: str = "consecutive_failures"


class ScheduleFired(ScheduleEvent):
    """Event emitted when any schedule source fires.

    Unified event for all schedule source types (TIME, WEBHOOK, DATASTORE).
    """

    event_type: str = "schedule.fired"
    payload: dict[str, Any]
    metadata: dict[str, Any] | None = None
    # Additional context for richer processing
    account_id: UUID | None = None  # For WEBHOOK schedules
    pod_id: UUID | None = None  # For pod-scoped table/file schedules
    scheduled_at: datetime | None = None  # For TIME schedules
    llm_output: dict[str, Any] | None = None  # For filtered events
    source_event_id: str | None = None


class ScheduleEvents:
    STREAM = "schedule_events"
    # Grouped consumers of this stream. Declared here (not just discovered via the
    # subscriber registry) so any process that PUBLISHES schedule events — the
    # scheduler pod, the API pod — can ensure these groups exist before XADD,
    # even though it never imports the consuming subscribers. Keeps a fired event
    # from being dropped when a consumer's group was lost (flush/failover) and is
    # otherwise only recreated later at "$".
    CONSUMER_GROUPS = (
        "workflow-schedule-events",
        "schedule-notifications",
        "surface-schedule-events",
    )
