"""Domain contracts for Agent Host identity.

This module covers pairing, host liveness, and harness snapshots. Run dispatch
(commands, leases, events) is layered on in a following change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.agent.domain.value_objects import JsonObject


AGENT_HOST_PROTOCOL_VERSION = 2
AGENT_HOST_OFFLINE_AFTER_SECONDS = 90


class AgentHostStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DRAINING = "DRAINING"
    UPGRADE_REQUIRED = "UPGRADE_REQUIRED"
    REVOKED = "REVOKED"


def effective_agent_host_status(
    status: AgentHostStatus | str,
    last_seen_at: datetime | None,
    *,
    now: datetime | None = None,
) -> AgentHostStatus:
    """Derive liveness from heartbeat freshness instead of stale DB state."""

    persisted = AgentHostStatus(status)
    if persisted in {
        AgentHostStatus.REVOKED,
        AgentHostStatus.UPGRADE_REQUIRED,
    }:
        return persisted
    timestamp = now or datetime.now(timezone.utc)
    if (
        last_seen_at is None
        or last_seen_at
        < timestamp - timedelta(seconds=AGENT_HOST_OFFLINE_AFTER_SECONDS)
    ):
        return AgentHostStatus.OFFLINE
    return persisted


class AgentHostHarnessHealth(str, Enum):
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    CONFIG_INVALID = "CONFIG_INVALID"
    PROBE_FAILED = "PROBE_FAILED"
    INSTALLING = "INSTALLING"
    DISABLED = "DISABLED"


class HostHello(BaseModel):
    installation_id: str = Field(min_length=1, max_length=255)
    host_release: str = Field(min_length=1, max_length=128)
    protocol_version: int = Field(ge=1)

    def negotiate(self, supported: int = AGENT_HOST_PROTOCOL_VERSION) -> int:
        if self.protocol_version == supported:
            return supported
        raise ValueError(
            f"Agent Host protocol {self.protocol_version} does not match "
            f"server protocol {supported}"
        )


class AgentHostCapacity(BaseModel):
    max_runs: int = Field(default=1, ge=0, le=128)
    active_runs: int = Field(default=0, ge=0, le=128)
    available_runs: int = Field(default=1, ge=0, le=128)

    @model_validator(mode="after")
    def validate_capacity(self) -> "AgentHostCapacity":
        if self.active_runs > self.max_runs:
            raise ValueError("active_runs cannot exceed max_runs")
        if self.available_runs > self.max_runs:
            raise ValueError("available_runs cannot exceed max_runs")
        return self


class AgentHostHarnessCapabilities(BaseModel):
    """Harness capabilities the server actually branches on.

    Only ``images`` changes server behaviour today (it adds the vision
    capability to the runtime picker). Anything else a host reports is kept
    verbatim by ``extra: allow`` rather than typed here, so the wire format
    stays open without inventing fields no code reads.
    """

    images: bool = False

    model_config = {"extra": "allow"}


class AgentHostConfigOption(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    current_value: object = None
    options: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class AgentHostHarnessSnapshot(BaseModel):
    harness_key: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    adapter_version: str = Field(min_length=1, max_length=128)
    upstream_version: str | None = Field(default=None, max_length=128)
    health: AgentHostHarnessHealth
    capabilities: AgentHostHarnessCapabilities = Field(
        default_factory=AgentHostHarnessCapabilities
    )
    config_revision: str = Field(min_length=1, max_length=255)
    config_options: list[AgentHostConfigOption] = Field(default_factory=list)
    stale_after: datetime
    stale_reason: str | None = None

    @field_validator("harness_key")
    @classmethod
    def normalize_harness_key(cls, value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        if not normalized:
            raise ValueError("harness_key cannot be empty")
        return normalized


class AgentHostPairingCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    organization_id: UUID | None = None


class AgentHostPairingCreated(BaseModel):
    pairing_id: UUID
    pairing_code: str
    expires_at: datetime


class AgentHostPairingComplete(BaseModel):
    pairing_code: str = Field(min_length=16, max_length=512)
    display_name: str = Field(min_length=1, max_length=255)
    hello: HostHello


class AgentHostPairingCompleted(BaseModel):
    host_id: UUID
    user_id: UUID
    organization_id: UUID | None
    host_secret: str


class AgentHostCommandKind(str, Enum):
    START_RUN = "START_RUN"
    CANCEL_RUN = "CANCEL_RUN"


class AgentHostCommandState(str, Enum):
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class AgentHostRejectionCode(str, Enum):
    DRAINING = "DRAINING"
    COMMAND_EXPIRED = "COMMAND_EXPIRED"
    HARNESS_NOT_FOUND = "HARNESS_NOT_FOUND"
    CONFIG_REVISION_STALE = "CONFIG_REVISION_STALE"
    CAPACITY_LOST = "CAPACITY_LOST"
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    INVALID_COMMAND = "INVALID_COMMAND"


class AgentHostRunState(str, Enum):
    QUEUED_FOR_HOST = "QUEUED_FOR_HOST"
    LEASED = "LEASED"
    ACCEPTED = "ACCEPTED"
    DISPATCHING = "DISPATCHING"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    WAITING_INPUT = "WAITING_INPUT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DISPATCH_UNKNOWN = "DISPATCH_UNKNOWN"


# States from which the host has not yet durably accepted the run; a
# pre-dispatch rejection or timeout here may safely fall back to a provider.
PRE_DISPATCH_AGENT_HOST_RUN_STATES = frozenset(
    {
        AgentHostRunState.QUEUED_FOR_HOST,
        AgentHostRunState.LEASED,
    }
)

# States (including WAITING_INPUT, which ends the host's turn while the
# conversation waits on a human) after which a lease no longer advances.
TERMINAL_AGENT_HOST_RUN_STATES = frozenset(
    {
        AgentHostRunState.WAITING_INPUT,
        AgentHostRunState.SUCCEEDED,
        AgentHostRunState.FAILED,
        AgentHostRunState.CANCELLED,
        AgentHostRunState.DISPATCH_UNKNOWN,
    }
)


_RUN_STATE_ORDER = {
    AgentHostRunState.QUEUED_FOR_HOST: 0,
    AgentHostRunState.LEASED: 1,
    AgentHostRunState.ACCEPTED: 2,
    AgentHostRunState.DISPATCHING: 3,
    AgentHostRunState.RUNNING: 4,
    AgentHostRunState.RECOVERING: 5,
    AgentHostRunState.WAITING_INPUT: 6,
    AgentHostRunState.SUCCEEDED: 7,
    AgentHostRunState.FAILED: 7,
    AgentHostRunState.CANCELLED: 7,
    AgentHostRunState.DISPATCH_UNKNOWN: 7,
}


def run_state_progresses(
    current: AgentHostRunState,
    reported: AgentHostRunState,
) -> bool:
    """Validate a host-reported state transition is not a regression."""

    if current in PRE_DISPATCH_AGENT_HOST_RUN_STATES:
        return reported not in PRE_DISPATCH_AGENT_HOST_RUN_STATES
    if current in {
        AgentHostRunState.RECOVERING,
        AgentHostRunState.WAITING_INPUT,
    } and reported is AgentHostRunState.RUNNING:
        return True
    return _RUN_STATE_ORDER[reported] >= _RUN_STATE_ORDER[current]


class AgentHostEventType(str, Enum):
    RUN_STATE = "run_state"
    USER_MESSAGE = "user_message"
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"
    AGENT_MESSAGE_UPSERT = "agent_message_upsert"
    AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
    AGENT_THOUGHT_UPSERT = "agent_thought_upsert"
    PLAN_UPSERT = "plan_upsert"
    TOOL_CALL_UPSERT = "tool_call_upsert"
    TOOL_CALL_UPDATE = "tool_call_update"
    USAGE_UPDATE = "usage_update"
    CONFIG_UPDATE = "config_update"
    PERMISSION_REQUEST = "permission_request"
    TERMINAL = "terminal"


class AgentHostRunCheckpoint(BaseModel):
    run_id: UUID
    lease_epoch: int = Field(ge=1)
    state: AgentHostRunState
    detail: JsonObject = Field(default_factory=dict)


class AgentHostCommandRejection(BaseModel):
    command_id: UUID
    run_id: UUID
    lease_epoch: int = Field(ge=1)
    code: AgentHostRejectionCode
    retryable: bool
    detail: str | None = Field(default=None, max_length=2048)


class AgentHostPollRequest(BaseModel):
    hello: HostHello
    capacity: AgentHostCapacity = Field(default_factory=AgentHostCapacity)
    acknowledged_command_ids: list[UUID] = Field(default_factory=list, max_length=256)
    checkpoints: list[AgentHostRunCheckpoint] = Field(
        default_factory=list, max_length=256
    )
    rejections: list[AgentHostCommandRejection] = Field(
        default_factory=list,
        max_length=256,
    )


class AgentHostRunSpec(BaseModel):
    agent_run_id: UUID
    conversation_id: UUID
    harness_id: UUID
    profile_revision: str = Field(min_length=1, max_length=255)
    model_name: str | None = Field(default=None, min_length=1, max_length=512)
    config_selections: JsonObject = Field(default_factory=dict)
    system_prompt: str
    prompt: list[JsonObject] = Field(min_length=1)
    context: JsonObject = Field(default_factory=dict)
    run_deadline: datetime


class AgentHostCommand(BaseModel):
    command_id: UUID
    kind: AgentHostCommandKind
    created_at: datetime
    expires_at: datetime
    run_id: UUID | None = None
    lease_epoch: int | None = Field(default=None, ge=1)
    payload: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_run_fencing(self) -> "AgentHostCommand":
        if self.kind in {
            AgentHostCommandKind.START_RUN,
            AgentHostCommandKind.CANCEL_RUN,
        } and (self.run_id is None or self.lease_epoch is None):
            raise ValueError(f"{self.kind.value} requires run_id and lease_epoch")
        return self


class AgentHostPollResponse(BaseModel):
    protocol_version: int = AGENT_HOST_PROTOCOL_VERSION
    host_status: AgentHostStatus
    commands: list[AgentHostCommand] = Field(default_factory=list)
    poll_after_ms: int = Field(default=0, ge=0, le=60_000)


class AgentHostEvent(BaseModel):
    """One run event on its way to the run's Redis Stream.

    There is no event id: events are deduplicated by ``sequence`` against the
    stream's watermark, which is what a resend after a Redis flush relies on.
    There is no host timestamp either -- a Redis stream id already embeds the
    millisecond it was appended.
    """

    run_id: UUID
    lease_epoch: int = Field(ge=1)
    sequence: int = Field(ge=1)
    type: AgentHostEventType
    object_id: str | None = Field(default=None, max_length=255)
    payload: JsonObject = Field(default_factory=dict)


class AgentHostEventBatch(BaseModel):
    events: Annotated[list[AgentHostEvent], Field(min_length=1, max_length=256)]

    @model_validator(mode="after")
    def validate_single_ordered_run(self) -> "AgentHostEventBatch":
        first = self.events[0]
        expected = first.sequence
        for event in self.events:
            if event.run_id != first.run_id or event.lease_epoch != first.lease_epoch:
                raise ValueError("event batch must contain one run and lease epoch")
            if event.sequence != expected:
                raise ValueError("event batch sequences must be contiguous and ordered")
            expected += 1
        return self


class AgentHostEventAck(BaseModel):
    run_id: UUID
    lease_epoch: int
    acked_through: int = Field(ge=0)
