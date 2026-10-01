"""Schedule API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, model_validator

from app.modules.schedule.config import schedule_settings
from app.core.authorization.delegation import POD_DEFAULT_AGENT_SELECTOR
from app.modules.schedule.domain.schedule import (
    TIME_SCHEDULE_FILTER_REFUSED,
    ScheduleRunStatus,
    ScheduleFireStatus,
    ScheduleType,
    normalize_datastore_schedule_config,
    refuses_filter,
)
from app.modules.schedule.domain.triage import (
    TIME_SCHEDULE_TRIAGE_REFUSED,
    TRIAGE_REPLACES_FILTER,
    TriageConfig,
    TriageRoute,
)

#: As long as `instruction` may be. The filter is asked as a decision, and a
#: decision's question holds 2,000 characters and its guidance 8,000, so an
#: instruction past the first is asked as guidance and none is ever cut.
FILTER_INSTRUCTION_MAX_CHARS = 8000
FILTER_INSTRUCTION_DESCRIPTION = (
    "WEBHOOK and DATASTORE schedules only: a yes/no condition, in your own "
    "words, that each event must meet to fire the schedule. It is asked as a "
    "decision (System One when configured, the system model otherwise), and "
    "every event it turns down is recorded as a FILTERED run carrying the "
    "decision's id. Refused on TIME schedules, which have no event to judge."
)
FILTER_OUTPUT_SCHEMA_DESCRIPTION = (
    "Optional JSON schema of fields to extract from an event the filter let "
    "through; the target reads them, with `should_proceed` and `decision_id`, "
    "as `llm_output`. Refused on TIME schedules."
)
TRIAGE_DESCRIPTION = (
    "WEBHOOK and DATASTORE schedules only, instead of a filter: a pod decider "
    "asked about each event, and what each option of its choice question does "
    "with it -- act (wake the target now), digest (hold it for the next digest, "
    "one run for many events), ask (hold it and ask the event's owner, whose "
    "answer routes it and teaches the decider) or ignore (record it as "
    "skipped). Every declared option must be routed."
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
        max_length=FILTER_INSTRUCTION_MAX_CHARS,
        description=FILTER_INSTRUCTION_DESCRIPTION,
    )
    filter_output_schema: dict | None = Field(
        default=None,
        description=FILTER_OUTPUT_SCHEMA_DESCRIPTION,
    )
    triage: TriageConfig | None = Field(default=None, description=TRIAGE_DESCRIPTION)
    visibility: str | None = None

    @model_validator(mode="after")
    def triage_replaces_the_filter(self) -> "CreateScheduleRequest":
        if self.triage is None:
            return self
        if self.schedule_type == ScheduleType.TIME:
            raise ValueError(TIME_SCHEDULE_TRIAGE_REFUSED)
        if self.filter_instruction or self.filter_output_schema:
            raise ValueError(TRIAGE_REPLACES_FILTER)
        return self

    @model_validator(mode="after")
    def require_one_target_name(self) -> "CreateScheduleRequest":
        if bool(self.agent_name) == bool(self.workflow_name):
            raise ValueError("Exactly one of agent_name or workflow_name is required")
        if self.connector_trigger_id and self.schedule_type != ScheduleType.WEBHOOK:
            raise ValueError("connector_trigger_id is only valid for WEBHOOK schedules")
        if refuses_filter(
            self.schedule_type,
            filter_instruction=self.filter_instruction,
            filter_output_schema=self.filter_output_schema,
        ):
            raise ValueError(TIME_SCHEDULE_FILTER_REFUSED)
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
    # Refused on a TIME schedule by the service, which knows the type this
    # request does not carry.
    filter_instruction: str | None = Field(
        default=None,
        max_length=FILTER_INSTRUCTION_MAX_CHARS,
        description=FILTER_INSTRUCTION_DESCRIPTION,
    )
    filter_output_schema: dict | None = Field(
        default=None, description=FILTER_OUTPUT_SCHEMA_DESCRIPTION
    )
    # Refused on a TIME schedule, and beside a filter, by the service, which
    # knows the type and the filter this request does not carry.
    triage: TriageConfig | None = Field(
        default=None,
        description=f"{TRIAGE_DESCRIPTION} Send null to remove it.",
    )
    is_active: bool | None = None
    visibility: str | None = None

    @property
    def clears_triage(self) -> bool:
        """`"triage": null` was sent, as distinct from leaving triage out."""
        return "triage" in self.model_fields_set and self.triage is None

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
    triage: TriageConfig | None = None
    #: When the triage's held events next go out together, if it has a digest.
    next_digest_at: datetime | None = None
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
    #: While `HELD`: what the event waits for -- its digest, or its person's answer.
    held_for: TriageRoute | None = None
    #: The one run a digest sent this event in, once it has.
    digest_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleRunListResponse(BaseModel):
    items: list[ScheduleRunResponse]
    limit: int


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
