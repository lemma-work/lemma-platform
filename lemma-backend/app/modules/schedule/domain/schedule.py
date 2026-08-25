from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.authorization.context import ResourceType
from app.core.authorization.delegation import POD_DEFAULT_AGENT_SELECTOR_ALIASES
from app.core.domain.entity import Entity
from app.modules.schedule.domain.match_conditions import (
    ColumnCondition,
    parse_match_conditions,
)
from app.modules.schedule.domain.value_objects import (
    DatastoreOperation,
    normalize_datastore_operations,
)


class ScheduleType(str, Enum):
    """Type of schedule source."""

    TIME = "TIME"  # Cron-based scheduling
    WEBHOOK = "WEBHOOK"  # External webhooks (Slack, Email, JIRA, custom)
    DATASTORE = "DATASTORE"  # Datastore row events


#: Said by the request schemas (as a field-scoped 422) and by the service (as a
#: 400, for bundle import, which never sees the schemas). One string, because a
#: person hitting this rule through the API and through a bundle should be told
#: the same thing.
INSTRUCTION_REQUIRED = (
    "Schedules targeting the default assistant require an instruction saying "
    "what it should do when they fire."
)


def is_pod_default_agent_target(agent_name: str | None) -> bool:
    """Whether this target name means the pod's default assistant.

    The default assistant has no `agents` row — it is synthesised from a
    conversation whose `agent_id` is null — so a schedule cannot name it
    through the `agent_id` foreign key the way it names every other agent.
    It is named on the wire by the same selector the conversation API already
    accepts, and stored as `targets_pod_default`.
    """
    return bool(agent_name) and agent_name in POD_DEFAULT_AGENT_SELECTOR_ALIASES


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

    table_name: str = Field(..., min_length=1, description="Table name to watch")
    operations: list[DatastoreOperation] | None = Field(
        default=None,
        description="Operations to watch. One or more of INSERT, UPDATE, DELETE.",
    )
    when: dict[str, ColumnCondition] | None = Field(
        default=None,
        description=(
            "Optional match conditions keyed by column name. Every condition "
            "must hold for the trigger to fire. Omit to fire on every row."
        ),
    )

    @field_validator("table_name")
    @classmethod
    def normalize_table_name(cls, value: str) -> str:
        table_name = value.strip()
        if not table_name:
            raise ValueError("table_name must not be blank")
        return table_name

    @field_validator("operations", mode="before")
    @classmethod
    def normalize_operations(cls, value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("operations must be a list")
        return normalize_datastore_operations(value)

    @field_validator("when", mode="before")
    @classmethod
    def normalize_when(cls, value):
        if value is None:
            return None
        return parse_match_conditions(value)

    @model_validator(mode="after")
    def _reject_undecidable_conditions(self) -> "DatastoreScheduleConfig":
        """Refuse a condition no declared operation could ever satisfy.

        A trigger that can never fire is the exact failure this feature exists
        to prevent, so it is rejected at save time rather than discovered by
        waiting for a run that does not come.
        """
        if not self.when or not self.operations:
            return self

        declared = set(self.operations)
        if DatastoreOperation.UPDATE not in declared:
            needs_update = sorted(
                column
                for column, condition in self.when.items()
                if condition.needs_prior_image
            )
            if needs_update:
                raise ValueError(
                    f"Conditions on {', '.join(needs_update)} use `changed`, "
                    "`written` or `from`, which only an UPDATE can satisfy. "
                    "Add UPDATE to operations, or match on the value instead."
                )

        if not declared & {DatastoreOperation.INSERT, DatastoreOperation.UPDATE}:
            needs_write = sorted(
                column
                for column, condition in self.when.items()
                if condition.needs_a_written_row
            )
            if needs_write:
                raise ValueError(
                    f"Conditions on {', '.join(needs_write)} use `to`, which "
                    "only an INSERT or UPDATE can satisfy. Add one of those to "
                    "operations, or match on the value instead."
                )
        return self


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
    normalized = {
        **config,
        "table_name": cfg.table_name,
        "operations": [op.value for op in cfg.operations],
    }
    if cfg.when is not None:
        # Store the expanded form so the saved config reads the same whether
        # the author wrote the `{"status": "approved"}` shorthand or spelled
        # the operator out, and `exclude_unset` keeps the operators nobody
        # supplied out of the blob.
        normalized["when"] = {
            column: condition.model_dump(by_alias=True, exclude_unset=True)
            for column, condition in cfg.when.items()
        }
    return normalized


class ScheduleFireStatus(str, Enum):
    """Outcome of the most recent fire attempt, recorded for debuggability."""

    TRIGGERED = "TRIGGERED"
    FILTERED = "FILTERED"
    ERROR = "ERROR"


class ScheduleRunStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    DISPATCHED = "DISPATCHED"
    COMPLETED = "COMPLETED"
    TARGET_FAILED = "TARGET_FAILED"
    CANCELLED = "CANCELLED"
    FILTERED = "FILTERED"
    FAILED = "FAILED"
    DEAD_LETTERED = "DEAD_LETTERED"


class ScheduleRunEntity(Entity):
    schedule_id: UUID
    user_id: UUID | None
    source_event_id: str
    status: ScheduleRunStatus
    attempts: int = 0
    target_kind: str
    target_run_id: str | None
    redrive_of_run_id: UUID | None = None
    redriven_by_user_id: UUID | None = None
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
    # The pod's default assistant as a target. It has no `agents` row, so it
    # cannot be named through `agent_id`; this flag is the third arm of the
    # target discriminator alongside those two ids.
    targets_pod_default: bool = False
    agent_name: str | None = None
    workflow_name: str | None = None
    # Type-specific config
    config: dict[str, Any] = Field(default_factory=dict)

    # What the target should do when this fires, in the author's own words.
    # Distinct from `filter_instruction`, which decides *whether* to fire:
    # this one directs the work once the firing is settled. It reaches an
    # agent target as the run's conversation instructions.
    instruction: str | None = None

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

    @property
    def has_target(self) -> bool:
        """Whether anything is wired to this schedule's firing."""
        return (
            self.agent_id is not None
            or self.workflow_id is not None
            or self.targets_pod_default
        )


class ScheduleCreateEntity(BaseModel):
    """Entity for creating a schedule."""

    user_id: UUID
    pod_id: UUID | None = None
    name: str | None = None
    schedule_type: ScheduleType
    agent_id: UUID | None = None
    workflow_id: UUID | None = None
    targets_pod_default: bool = False
    agent_name: str | None = None
    workflow_name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    instruction: str | None = None
    filter_instruction: str | None = None
    filter_output_schema: dict[str, Any] | None = None
    account_id: UUID | None = None
    connector_trigger_id: str | None = None
    # None means "caller did not specify": DATASTORE and GLOBAL-workflow
    # schedules default to POD; other schedules default to PERSONAL.
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
    targets_pod_default: bool | None = None
    agent_name: str | None = None
    workflow_name: str | None = None
    instruction: str | None = None
    filter_instruction: str | None = None
    filter_output_schema: dict[str, Any] | None = None
    is_active: bool | None = None
    visibility: str | None = None
