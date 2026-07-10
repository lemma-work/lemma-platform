from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.authorization.context import ResourceType
from app.core.domain.entity import Entity
from app.modules.schedule.domain.value_objects import (
    DatastoreOperation,
    normalize_datastore_operations,
)

class ScheduleType(str, Enum):
    """Type of schedule source."""

    TIME = "TIME"  # Cron-based scheduling
    WEBHOOK = "WEBHOOK"  # External webhooks (Slack, Email, JIRA, custom)
    DATASTORE = "DATASTORE"  # Datastore row events


class TimeScheduleConfig(BaseModel):
    """Configuration for time-based schedules."""

    cron: str | None = Field(None, description="Cron expression for scheduling")
    scheduled_at: str | None = Field(
        None, description="ISO format date for one-time schedule"
    )


class WebhookScheduleConfig(BaseModel):
    """Configuration for webhook-based schedules."""

    source: str = Field(
        ..., description="Source of the webhook (e.g., slack, composio)"
    )
    # Additional dynamic fields can be stored in the config dict,
    # but source is required.


class DatastoreScheduleConfig(BaseModel):
    """Configuration for datastore-based schedules."""

    table_name: str | None = Field(None, description="Table name to watch")
    operations: list[DatastoreOperation] | None = Field(
        default=None,
        description="Operations to watch. One or more of INSERT, UPDATE, DELETE.",
    )

    @field_validator("operations", mode="before")
    @classmethod
    def normalize_operations(cls, value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("operations must be a list")
        return normalize_datastore_operations(value)


def normalize_datastore_schedule_config(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a DATASTORE schedule config for storage.

    Operations are required and explicit: workflows are often not built to
    handle every operation's payload shape, so the author must declare which
    operations the schedule reacts to. Raises ValueError when operations are
    missing, empty, or invalid.
    """
    cfg = DatastoreScheduleConfig(**config)
    if not cfg.operations:
        raise ValueError(
            "DATASTORE schedules must declare operations explicitly. "
            "Valid values: INSERT, UPDATE, DELETE."
        )
    return {**config, "operations": [op.value for op in cfg.operations]}


class ScheduleFireStatus(str, Enum):
    """Outcome of the most recent fire attempt, recorded for debuggability."""

    TRIGGERED = "TRIGGERED"
    FILTERED = "FILTERED"
    ERROR = "ERROR"


class ScheduleRunStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    DISPATCHED = "DISPATCHED"
    FILTERED = "FILTERED"
    FAILED = "FAILED"
    DEAD_LETTERED = "DEAD_LETTERED"


class ScheduleRunEntity(Entity):
    schedule_id: UUID
    source_event_id: str
    status: ScheduleRunStatus
    attempts: int = 0
    target_kind: str
    target_run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    llm_output: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = None
    error_code: str | None = None
    source_occurred_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ScheduleEntity(Entity):
    """Schedule entity for time and event-driven activation."""

    resource_type: ClassVar[ResourceType] = ResourceType.SCHEDULE

    user_id: UUID = Field(..., description="User ID owning the schedule")
    pod_id: UUID | None = None
    name: str | None = None
    schedule_type: ScheduleType
    agent_id: UUID | None = None
    workflow_id: UUID | None = None
    agent_name: str | None = None
    workflow_name: str | None = None
    # Type-specific config
    config: dict[str, Any] = Field(default_factory=dict)

    # LLM-based event filtering
    filter_instruction: str | None = None
    filter_output_schema: dict[str, Any] | None = None

    # For WEBHOOK schedules backed by connector provider triggers.
    account_id: UUID | None = None
    connector_trigger_id: str | None = None

    visibility: str = "POD"
    is_active: bool = True
    is_internal: bool = (
        False  # Internal schedules are created by flow execution for waits/timeouts.
    )
    allowed_actions: list[str] = Field(default_factory=list)

    # Fire telemetry — written on every fire attempt so "why didn't my
    # schedule fire" is answerable without DB access.
    last_fired_at: datetime | None = None
    last_run_id: str | None = None
    last_fire_status: ScheduleFireStatus | None = None
    last_error: str | None = None
    consecutive_failures: int = 0

    @property
    def time_config(self) -> TimeScheduleConfig | None:
        if self.schedule_type == ScheduleType.TIME:
            return TimeScheduleConfig(**self.config)
        return None

    @property
    def webhook_config(self) -> WebhookScheduleConfig | None:
        if self.schedule_type == ScheduleType.WEBHOOK:
            return WebhookScheduleConfig(**self.config)
        return None

    @property
    def datastore_config(self) -> DatastoreScheduleConfig | None:
        if self.schedule_type == ScheduleType.DATASTORE:
            return DatastoreScheduleConfig(**self.config)
        return None


class ScheduleCreateEntity(BaseModel):
    """Entity for creating a schedule."""

    user_id: UUID
    pod_id: UUID | None = None
    name: str | None = None
    schedule_type: ScheduleType
    agent_id: UUID | None = None
    workflow_id: UUID | None = None
    agent_name: str | None = None
    workflow_name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    filter_instruction: str | None = None
    filter_output_schema: dict[str, Any] | None = None
    account_id: UUID | None = None
    connector_trigger_id: str | None = None
    # None means "caller did not specify": the service derives the default
    # (PERSONAL, or POD when the schedule targets a GLOBAL workflow).
    visibility: str | None = None
    is_internal: bool = False

    @model_validator(mode="after")
    def _require_explicit_datastore_operations(self) -> "ScheduleCreateEntity":
        if self.schedule_type == ScheduleType.DATASTORE:
            self.config = normalize_datastore_schedule_config(self.config)
        return self


class ScheduleUpdateEntity(BaseModel):
    """Entity for updating a schedule."""

    config: dict[str, Any] | None = None
    name: str | None = None
    agent_id: UUID | None = None
    workflow_id: UUID | None = None
    agent_name: str | None = None
    workflow_name: str | None = None
    filter_instruction: str | None = None
    filter_output_schema: dict[str, Any] | None = None
    is_active: bool | None = None
    visibility: str | None = None
