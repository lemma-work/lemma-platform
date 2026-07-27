"""Domain contracts for the durable external Agent Host protocol."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.agent.domain.value_objects import JsonObject


AGENT_HOST_PROTOCOL_VERSION = 2
AGENT_HOST_OFFLINE_AFTER_SECONDS = 90
FOLLOW_ADAPTER_DEFAULT = "FOLLOW_ADAPTER_DEFAULT"


class AgentHostStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DRAINING = "DRAINING"
    DEGRADED = "DEGRADED"
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


class AgentHostIntegrationHealth(str, Enum):
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    CONFIG_INVALID = "CONFIG_INVALID"
    PROBE_FAILED = "PROBE_FAILED"
    INSTALLING = "INSTALLING"
    DISABLED = "DISABLED"


class AgentHostAdapterProtocol(str, Enum):
    ACP_V1 = "ACP_V1"
    NATIVE = "NATIVE"


class AgentHostCommandKind(str, Enum):
    START_RUN = "START_RUN"
    CANCEL_RUN = "CANCEL_RUN"
    DRAIN = "DRAIN"
    RESUME = "RESUME"
    REFRESH_INTEGRATION = "REFRESH_INTEGRATION"
    CLOSE_SESSION = "CLOSE_SESSION"
    ROTATE_DEVICE_KEY = "ROTATE_DEVICE_KEY"


class AgentHostCommandState(str, Enum):
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


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


TERMINAL_AGENT_HOST_RUN_STATES = frozenset(
    {
        AgentHostRunState.WAITING_INPUT,
        AgentHostRunState.SUCCEEDED,
        AgentHostRunState.FAILED,
        AgentHostRunState.CANCELLED,
        AgentHostRunState.DISPATCH_UNKNOWN,
    }
)


class AgentHostCheckpoint(str, Enum):
    ACCEPTED = "ACCEPTED"
    DISPATCH_INTENT = "DISPATCH_INTENT"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    TERMINAL = "TERMINAL"


_CHECKPOINT_ORDER = {
    AgentHostCheckpoint.ACCEPTED: 1,
    AgentHostCheckpoint.DISPATCH_INTENT: 2,
    AgentHostCheckpoint.PROVIDER_ACCEPTED: 3,
    AgentHostCheckpoint.RUNNING: 4,
    AgentHostCheckpoint.RECOVERING: 5,
    AgentHostCheckpoint.TERMINAL: 6,
}


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
    INPUT_REQUEST = "input_request"
    WARNING = "warning"
    TERMINAL = "terminal"


class HostHello(BaseModel):
    protocol_min: int = Field(ge=1)
    protocol_max: int = Field(ge=1)
    host_release: str = Field(min_length=1, max_length=128)
    adapter_manifest_id: str = Field(min_length=1, max_length=255)
    installation_id: str = Field(min_length=1, max_length=255)
    instance_id: UUID

    @model_validator(mode="after")
    def validate_protocol_range(self) -> "HostHello":
        if self.protocol_min > self.protocol_max:
            raise ValueError("protocol_min cannot exceed protocol_max")
        return self

    def negotiate(self, supported: int = AGENT_HOST_PROTOCOL_VERSION) -> int:
        if self.protocol_min <= supported <= self.protocol_max:
            return supported
        raise ValueError(
            f"Agent Host protocol range {self.protocol_min}-{self.protocol_max} "
            f"does not include server protocol {supported}"
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


class AgentHostIntegrationCapabilities(BaseModel):
    load_session: bool = False
    resume_session: bool = False
    close_session: bool = False
    images: bool = False
    plans: bool = False
    usage: bool = False
    durable_session_recovery: bool = False

    model_config = ConfigDict(extra="allow")


class AgentHostConfigOption(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    current_value: object = None
    options: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class AgentHostIntegrationSnapshot(BaseModel):
    integration_key: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    adapter_protocol: AgentHostAdapterProtocol
    adapter_version: str = Field(min_length=1, max_length=128)
    upstream_version: str | None = Field(default=None, max_length=128)
    auth_state: str = Field(min_length=1, max_length=64)
    health: AgentHostIntegrationHealth
    capabilities: AgentHostIntegrationCapabilities = Field(
        default_factory=AgentHostIntegrationCapabilities
    )
    config_revision: str = Field(min_length=1, max_length=255)
    config_options: list[AgentHostConfigOption] = Field(default_factory=list)
    fetched_at: datetime
    stale_after: datetime
    stale_reason: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("integration_key")
    @classmethod
    def normalize_integration_key(cls, value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        if not normalized:
            raise ValueError("integration_key cannot be empty")
        return normalized


class AgentHostRunCheckpoint(BaseModel):
    run_id: UUID
    lease_epoch: int = Field(ge=1)
    checkpoint: AgentHostCheckpoint
    state: AgentHostRunState
    detail: JsonObject = Field(default_factory=dict)


class AgentHostPollRequest(BaseModel):
    hello: HostHello
    capacity: AgentHostCapacity = Field(default_factory=AgentHostCapacity)
    acknowledged_command_ids: list[UUID] = Field(default_factory=list, max_length=256)
    checkpoints: list[AgentHostRunCheckpoint] = Field(default_factory=list, max_length=256)


class AgentHostRunSpec(BaseModel):
    agent_run_id: UUID
    conversation_id: UUID
    integration_id: UUID
    profile_revision: str = Field(min_length=1, max_length=255)
    config_selections: JsonObject = Field(default_factory=dict)
    system_prompt: str
    prompt: list[JsonObject] = Field(min_length=1)
    context: JsonObject = Field(default_factory=dict)
    mcp_route_id: str = Field(min_length=1, max_length=255)
    run_deadline: datetime


class AgentHostCommand(BaseModel):
    command_id: UUID
    kind: AgentHostCommandKind
    created_at: datetime
    expires_at: datetime
    run_id: UUID | None = None
    lease_epoch: int | None = Field(default=None, ge=1)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_run_fencing(self) -> "AgentHostCommand":
        run_command = self.kind in {
            AgentHostCommandKind.START_RUN,
            AgentHostCommandKind.CANCEL_RUN,
            AgentHostCommandKind.RESUME,
        }
        if run_command and (self.run_id is None or self.lease_epoch is None):
            raise ValueError(f"{self.kind.value} requires run_id and lease_epoch")
        return self


class AgentHostPollResponse(BaseModel):
    protocol_version: int = AGENT_HOST_PROTOCOL_VERSION
    policy_revision: str
    host_status: AgentHostStatus
    commands: list[AgentHostCommand] = Field(default_factory=list)
    poll_after_ms: int = Field(default=0, ge=0, le=60_000)


class AgentHostEvent(BaseModel):
    run_id: UUID
    lease_epoch: int = Field(ge=1)
    sequence: int = Field(ge=1)
    event_id: UUID
    occurred_at: datetime
    type: AgentHostEventType
    object_id: str | None = Field(default=None, max_length=255)
    payload: JsonObject = Field(default_factory=dict)
    integration_key: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=128)

    @property
    def payload_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


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


class AgentHostPairingCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    organization_id: UUID | None = None


class AgentHostPairingCreated(BaseModel):
    pairing_id: UUID
    pairing_code: str
    expires_at: datetime


class AgentHostPairingComplete(BaseModel):
    pairing_code: str = Field(min_length=16, max_length=512)
    public_key: str = Field(min_length=32, max_length=512)
    display_name: str = Field(min_length=1, max_length=255)
    hello: HostHello
    nonce: str = Field(min_length=16, max_length=255)
    timestamp: int = Field(gt=0)
    signature: str = Field(min_length=32, max_length=512)


class AgentHostPairingCompleted(BaseModel):
    host_id: UUID
    user_id: UUID
    organization_id: UUID | None
    public_key_fingerprint: str


class AgentHostTokenExchange(BaseModel):
    host_id: UUID
    nonce: str = Field(min_length=16, max_length=255)
    timestamp: int = Field(gt=0)
    signature: str = Field(min_length=32, max_length=512)


class AgentHostTokenResponse(BaseModel):
    access_token: str
    expires_at: datetime


class AgentHostTokenClaims(BaseModel):
    host_id: UUID
    user_id: UUID
    organization_id: UUID | None
    expires_at_epoch: int
    capabilities: tuple[
        Literal["control", "events", "integrations", "mcp"],
        ...,
    ]


class AgentHostMcpRouteResponse(BaseModel):
    route_id: UUID
    run_id: UUID
    lease_epoch: int = Field(ge=1)
    expires_at: datetime
    mcp: JsonObject


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_agent_host_selections(
    *,
    config_options: list[object],
    selections: JsonObject,
) -> JsonObject:
    """Validate provider-owned selections without translating their values."""
    options_by_key: dict[str, dict[str, object]] = {}
    for raw_option in config_options:
        if not isinstance(raw_option, dict):
            continue
        option_id = str(raw_option.get("id") or "").strip()
        category = str(raw_option.get("category") or "").strip()
        if option_id:
            options_by_key[option_id] = raw_option
        if category:
            options_by_key[category] = raw_option

    normalized: JsonObject = {}
    for key, value in selections.items():
        normalized_key = str(key).strip()
        option = options_by_key.get(normalized_key)
        if option is None:
            raise ValueError(f"Unknown Agent Host configuration selection: {key}")
        if value == FOLLOW_ADAPTER_DEFAULT:
            normalized[normalized_key] = value
            continue
        allowed_values = _agent_host_option_values(option.get("options"))
        if allowed_values and value not in allowed_values:
            raise ValueError(
                f"Invalid value for Agent Host configuration selection: {key}"
            )
        normalized[normalized_key] = value
    return normalized


def _agent_host_option_values(raw_options: object) -> list[object]:
    if not isinstance(raw_options, list):
        return []
    values: list[object] = []
    for item in raw_options:
        if not isinstance(item, dict):
            values.append(item)
            continue
        if "value" in item:
            values.append(item["value"])
        elif "id" in item:
            values.append(item["id"])
    return values


def checkpoint_advances(
    previous: AgentHostCheckpoint | None,
    requested: AgentHostCheckpoint,
) -> bool:
    if previous is None:
        return requested is AgentHostCheckpoint.ACCEPTED
    if (
        previous is AgentHostCheckpoint.RECOVERING
        and requested is AgentHostCheckpoint.RUNNING
    ):
        return True
    return _CHECKPOINT_ORDER[requested] >= _CHECKPOINT_ORDER[previous]
