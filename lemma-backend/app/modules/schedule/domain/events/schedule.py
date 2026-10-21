from typing import Any
from uuid import UUID
from app.core.domain.events import DomainEvent
from app.modules.schedule.domain.schedule import ScheduleType


class ScheduleEvent(DomainEvent):
    """Base for every event on the ``schedule_events`` stream.

    ``user_id`` is declared per event rather than here, so an event that may
    not know the owner can say so without weakening the base contract.
    """

    schedule_id: UUID
    schedule_type: ScheduleType

    @classmethod
    def stream_name(cls) -> str:
        return "schedule_events"


class ScheduleLifecycleEvent(ScheduleEvent):
    """A change to the schedule itself, always attributed to its owner."""

    user_id: UUID
    #: Optional because a schedule is not required to belong to a pod, not
    #: because it is unknown -- the owning pod is on the entity at every
    #: lifecycle point.
    pod_id: UUID | None = None


class ScheduleCreated(ScheduleLifecycleEvent):
    event_type: str = "schedule.created"
    config: dict[str, Any]


class ScheduleUpdated(ScheduleLifecycleEvent):
    event_type: str = "schedule.updated"
    config: dict[str, Any]


class ScheduleDeleted(ScheduleLifecycleEvent):
    event_type: str = "schedule.deleted"


class ScheduleRunCompleted(ScheduleEvent):
    """One scheduled run reached a terminal outcome.

    Not a lifecycle event: it says nothing about the schedule's definition.

    It does carry a person. Nobody is *watching* a scheduled run, which is not
    the same as nobody being behind it: the work is done for the schedule's
    owner, or -- for a datastore trigger on an RLS table -- for whoever owns the
    row that fired it. Reporting these as machine work took every scheduled
    outcome off its owner's timeline.
    """

    event_type: str = "schedule.run.completed"
    pod_id: UUID | None = None
    status: str
    #: Who the run was for. The ledger row carries the row owner for a datastore
    #: trigger on an RLS table and the schedule owner otherwise, so this is a real
    #: person in almost every case -- a scheduled run is unattended, not
    #: unattributed.
    user_id: UUID | None = None


class ScheduleDeactivated(ScheduleLifecycleEvent):
    """A schedule was auto-deactivated by the failure circuit breaker.

    Emitted when a schedule hits the consecutive-failure threshold and is set
    inactive. Consumed today to notify the creator; the event is the extension
    point for future reactions (in-app notification, admin alerting) without
    changing the breaker.
    """

    event_type: str = "schedule.deactivated"
    consecutive_failures: int
    reason: str = "consecutive_failures"


class ScheduleEvents:
    STREAM = "schedule_events"
    # Grouped consumers of this stream. Declared here (not just discovered via the
    # subscriber registry) so any process that PUBLISHES schedule events — the
    # scheduler pod, the API pod — can ensure these groups exist before XADD,
    # even though it never imports the consuming subscribers. Keeps a fired event
    # from being dropped when a consumer's group was lost (flush/failover) and is
    # otherwise only recreated later at "$".
    CONSUMER_GROUPS = ("workflow-schedule-events",)
