"""Schedule work kinds and their payloads. Ids, not snapshots, wherever a row exists.

``schedule.filter`` is the exception: a webhook delivery has no row until the
filter decides, so the event rides in the payload until it is claimed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from app.core.infrastructure.work import SYSTEM_NAMESPACE, pod_namespace

#: Write the next ``schedule.fire`` for a TIME schedule whose cursor is missing
#: or was set outside the write paths (cutover seeding, legacy rows).
SCHEDULE_ARM = "schedule.arm"
#: One occurrence of a TIME schedule, due at ``fire_at``.
SCHEDULE_FIRE = "schedule.fire"
#: Start the target of a reserved run.
SCHEDULE_START = "schedule.start_target"
#: Ask a schedule's LLM filter about one event.
SCHEDULE_FILTER = "schedule.filter"
#: Record a target's terminal outcome on its run.
SCHEDULE_OUTCOME = "schedule.outcome"
#: A schedule-started workflow run finished / could not be started (facts).
SCHEDULE_WORKFLOW_FINISHED = "schedule.workflow_finished"
SCHEDULE_WORKFLOW_REFUSED = "schedule.workflow_refused"
#: Tell a schedule's owner the platform paused it.
SCHEDULE_PAUSED_NOTICE = "schedule.paused_notice"
#: Periodic: re-arm any active TIME schedule left without a fire item.
SCHEDULE_ARM_AUDIT = "schedule.arm_audit"

#: ``paused_reason`` of a TIME schedule whose fire item ran out of tries.
FIRE_FAILED = "fire_failed"


class ArmAudit(BaseModel):
    pass


class ScheduleRef(BaseModel):
    schedule_id: UUID


class TimeFire(BaseModel):
    schedule_id: UUID
    #: The occurrence this item is for. The handler fires only while the
    #: schedule's cursor still says exactly this, so an edit, a pause or a
    #: delete made since turns the item into a no-op.
    fire_at: datetime


class StartTarget(BaseModel):
    schedule_run_id: UUID


class FilterEvent(BaseModel):
    schedule_id: UUID
    source_event_id: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    #: The run's owner when it is not the schedule's (a table change's row owner).
    user_id: UUID | None = None
    source_occurred_at: datetime | None = None


class TargetOutcome(BaseModel):
    target_kind: str
    target_run_id: str
    status: str
    completed_at: datetime | None = None
    error_type: str | None = None


class PausedNotice(BaseModel):
    schedule_id: UUID
    user_id: UUID
    reason: str
    consecutive_failures: int = 0


def schedule_namespace(pod_id: UUID | None) -> str:
    """A schedule's work runs in its pod's namespace; pod-less ones in ``system``."""
    return pod_namespace(pod_id) if pod_id is not None else SYSTEM_NAMESPACE


def fire_dedup_key(schedule_id: UUID, fire_at: datetime) -> str:
    return f"schedule:{schedule_id}:{fire_at.astimezone(timezone.utc).isoformat()}"


def time_source_event_id(schedule_id: UUID, fire_at: datetime) -> str:
    """The run's source key for a TIME occurrence; unchanged from the poller's."""
    return f"cron:{schedule_id}:{fire_at.astimezone(timezone.utc).isoformat()}"


def schedule_key(schedule_id: UUID) -> str:
    """Concurrency and rate key for one trigger."""
    return f"schedule:{schedule_id}"
