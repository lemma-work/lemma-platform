"""Domain contracts for Agent Host identity.

This module covers pairing, host liveness, and harness snapshots. Run dispatch
(commands, leases, events) is layered on in a following change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
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
