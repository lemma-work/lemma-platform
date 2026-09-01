"""Schedule API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, model_validator

from app.modules.schedule.config import schedule_settings
from app.core.authorization.delegation import POD_DEFAULT_AGENT_SELECTOR
from app.modules.schedule.domain.schedule import (
    ScheduleRunStatus,
    ScheduleFireStatus,
    ScheduleType,
    normalize_datastore_schedule_config,
)


class CreateScheduleRequest(BaseModel):
    """Request to create a pod schedule."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Stable pod-scoped schedule name used for import/export upserts.",
    )
    schedule_type: ScheduleType
    agent_name: str | None = Field(
        default=None,
        description=(
            f"Pod agent to wake, by name. Pass '{POD_DEFAULT_AGENT_SELECTOR}' "
            "(or 'pod_default') to wake the pod's default assistant, which has "
            "no name of its own."
        ),
    )
    workflow_name: str | None = None
    config: dict = Field(default_factory=dict)
    instruction: str | None = Field(
        default=None,
        max_length=8000,
        description=(
            "What the target should do when this fires, in your own words. "
            "Reaches an agent as the run's conversation instructions, layered "
            "after the agent's own. Required when targeting the default "
            "assistant, which has no standing instruction to fall back on. "
            "Distinct from filter_instruction, which decides whether to fire."
        ),
    )
    account_id: UUID | None = Field(
        default=None,
        description=(
            "Connected connector account used to provision provider-backed webhook "
            "schedules."
        ),
    )
    connector_trigger_id: str | None = Field(
        default=None,
        description=(
            "Connector trigger id for agent WEBHOOK schedules. Do not provide this "
            "for workflow schedules; workflow WEBHOOK schedules derive it from the "
            "workflow start configuration."
        ),
    )
    filter_instruction: str | None = Field(
        default=None,
        description=(
            "Optional schedule-level LLM filter instruction. Filters belong to the "
            "schedule, not the workflow start."
        ),
    )
    filter_output_schema: dict | None = Field(
        default=None,
        description=(
            "Optional schema for the schedule-level filter output. Filters belong to "
            "the schedule, not the workflow start."
        ),
    )
    visibility: str | None = None

    @model_validator(mode="after")
    def require_one_target_name(self) -> "CreateScheduleRequest":
        if bool(self.agent_name) == bool(self.workflow_name):
            raise ValueError("Exactly one of agent_name or workflow_name is required")
        if self.connector_trigger_id and self.schedule_type != ScheduleType.WEBHOOK:
            raise ValueError("connector_trigger_id is only valid for WEBHOOK schedules")
        if (
            self.agent_name
            and self.schedule_type == ScheduleType.WEBHOOK
            and not self.connector_trigger_id
        ):
            raise ValueError("Agent webhook schedules require connector_trigger_id")
        # "A target with no standing instruction must be told what to do" is
        # enforced in the service, not here: it is a question about the resolved
        # agent, and a validator cannot look one up. The cost is that it comes
        # back as a 400 rather than a field-scoped 422.
        if self.workflow_name and self.connector_trigger_id:
            raise ValueError(
                "connector_trigger_id is only valid for agent webhook schedules; "
                "workflow schedules derive it from the workflow start config"
            )
        if self.schedule_type == ScheduleType.DATASTORE:
            self.config = normalize_datastore_schedule_config(self.config)
        return self


class UpdateScheduleRequest(BaseModel):
    """Request to update a schedule."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict | None = None
    agent_name: str | None = None
    workflow_name: str | None = None
    instruction: str | None = Field(default=None, max_length=8000)
    filter_instruction: str | None = None
    filter_output_schema: dict | None = None
    is_active: bool | None = None
    visibility: str | None = None

    @model_validator(mode="after")
    def allow_at_most_one_target_name(self) -> "UpdateScheduleRequest":
        if self.agent_name and self.workflow_name:
            raise ValueError("Only one of agent_name or workflow_name can be provided")
        # schedule_type is unknown here; the service enforces the DATASTORE
        # rules. Normalize eagerly when the config is recognizably datastore
        # so users get a field-scoped 422 instead of a service-level 400.
        if self.config is not None and "operations" in self.config:
            self.config = normalize_datastore_schedule_config(self.config)
        return self


class ScheduleResponse(BaseModel):
    """Schedule response."""

    id: UUID
    user_id: UUID
    pod_id: UUID | None
    name: str | None
    schedule_type: ScheduleType
    agent_id: UUID | None
    workflow_id: UUID | None
    # `POD_DEFAULT` when the target is the pod's own assistant. That is the
    # selector the API takes rather than the row's internal name, so a client
    # reads back exactly what it wrote.
    agent_name: str | None = None
    workflow_name: str | None = None
    config: dict
    instruction: str | None = None
    account_id: UUID | None
    connector_trigger_id: str | None
    filter_instruction: str | None
    filter_output_schema: dict | None
    visibility: str
    is_active: bool
    is_internal: bool
    last_fired_at: datetime | None = None
    last_run_id: str | None = None
    last_fire_status: ScheduleFireStatus | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    # A breaker pause and a deliberate one were indistinguishable on the wire:
    # ``is_active`` goes false either way, and the pod overview drops inactive
    # schedules entirely. "The user never sees why their schedules stopped" was
    # that, not a missing signal — the failures have been recorded, emailed and
    # served all along, with nothing tying them to the pause.
    #
    # Derived rather than stored so it cannot drift from the breaker's own rule,
    # and so it needs no migration: both halves are already persisted.
    @computed_field(  # type: ignore[prop-decorator]
        description=(
            "True when the failure breaker paused this schedule, as opposed to "
            "a person pausing it. Reactivating resets the failure count."
        )
    )
    @property
    def paused_by_failures(self) -> bool:
        threshold = schedule_settings.schedule_max_consecutive_failures
        return (
            not self.is_active
            and threshold > 0
            and self.consecutive_failures >= threshold
        )


class ScheduleDetailResponse(ScheduleResponse):
    """Schedule detail response."""

    allowed_actions: list[str] = Field(default_factory=list)


class ScheduleListResponse(BaseModel):
    """Schedule list response."""

    items: list[ScheduleDetailResponse]
    limit: int
    next_page_token: str | None = None


class ScheduleRunResponse(BaseModel):
    id: UUID
    schedule_id: UUID
    user_id: UUID | None
    source_event_id: str
    status: ScheduleRunStatus
    attempts: int
    target_kind: str
    target_run_id: str | None
    redrive_of_run_id: UUID | None = None
    redriven_by_user_id: UUID | None = None
    payload: dict
    metadata: dict
    llm_output: dict
    error_type: str | None = None
    error_code: str | None = None
    source_occurred_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleRunListResponse(BaseModel):
    items: list[ScheduleRunResponse]
    limit: int


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
