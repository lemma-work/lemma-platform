from datetime import datetime
from typing import Any
from uuid import UUID
from app.core.domain.events import DomainEvent
from app.modules.schedule.domain.schedule import ScheduleType


class ScheduleEvent(DomainEvent):
    """Base for every event on the ``schedule_events`` stream.

    ``user_id`` is declared per event rather than here. Lifecycle events always
    know the schedule owner, but ``ScheduleFired`` has one case that genuinely
    cannot — see its docstring — and promising it on the base only to weaken it
    in a subclass would make the base contract a lie.
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

    Not a lifecycle event: it says nothing about the schedule's definition, and
    it carries no ``user_id`` because a scheduled run has no person on it -- the
    whole point of a schedule is that it runs when nobody is there.
    """

    event_type: str = "schedule.run.completed"
    pod_id: UUID | None = None
    status: str


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


class ScheduleFired(ScheduleEvent):
    """Event emitted when any schedule source fires.

    Unified event for all schedule source types (TIME, WEBHOOK, DATASTORE).
    ``user_id`` is the single owner of the resulting schedule/target run: the
    row owner for RLS datastore events, otherwise the schedule owner.

    It is optional for exactly one case: a workflow wait timer persisted before
    ownership existed. Those fires carry ``payload.workflow_run_id``, and the
    run row is the authoritative owner, so nothing has to be synthesized. A
    fire that starts a *schedule* run still fails closed without an owner —
    see ``ScheduleStartService``.
    """

    event_type: str = "schedule.fired"
    user_id: UUID | None = None
    payload: dict[str, Any]
    source_event_id: str
    metadata: dict[str, Any] | None = None
    # Additional context for richer processing
    account_id: UUID | None = None  # For WEBHOOK schedules
    pod_id: UUID | None = None  # For pod-scoped table/file schedules
    scheduled_at: datetime | None = None  # For TIME schedules
    llm_output: dict[str, Any] | None = None  # For filtered events


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
        "schedule-runtime-lifecycle",
        "surface-schedule-events",
    )
