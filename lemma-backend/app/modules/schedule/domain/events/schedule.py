from datetime import datetime
from uuid import UUID
from app.core.domain.events import DomainEvent
from app.modules.schedule.domain.schedule import ScheduleType


class ScheduleEvent(DomainEvent):
    schedule_id: UUID
    user_id: UUID
    schedule_type: ScheduleType


class ScheduleCreated(ScheduleEvent):
    event_type: str = "schedule.created"
    config: dict


class ScheduleUpdated(ScheduleEvent):
    event_type: str = "schedule.updated"
    config: dict


class ScheduleDeleted(ScheduleEvent):
    event_type: str = "schedule.deleted"


class ScheduleFired(ScheduleEvent):
    """Event emitted when any schedule source fires.

    Unified event for all schedule source types (TIME, WEBHOOK, DATASTORE).
    """

    event_type: str = "schedule.fired"
    payload: dict
    metadata: dict | None = None
    # Additional context for richer processing
    account_id: UUID | None = None  # For WEBHOOK schedules
    pod_id: UUID | None = None  # For pod-scoped table/file schedules
    scheduled_at: datetime | None = None  # For TIME schedules
    llm_output: dict | None = None  # For filtered events


class ScheduleEvents:
    STREAM = "schedule_events"
    # Grouped consumers of this stream. Declared here (not just discovered via the
    # subscriber registry) so any process that PUBLISHES schedule events — the
    # scheduler pod, the API pod — can ensure these groups exist before XADD,
    # even though it never imports the consuming subscribers. Keeps a fired event
    # from being dropped when a consumer's group was lost (flush/failover) and is
    # otherwise only recreated later at "$". The workflow pod consumes via this
    # group; the surface subscriber reads group-less (fan-out) and needs none.
    CONSUMER_GROUPS = ("workflow-schedule-events",)
