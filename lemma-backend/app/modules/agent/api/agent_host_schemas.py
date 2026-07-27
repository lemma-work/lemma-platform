"""HTTP response/request shapes for Agent Host management APIs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.agent_host import (
    AgentHostIntegrationSnapshot,
    AgentHostStatus,
)


class AgentHostIntegrationResponse(BaseModel):
    id: UUID
    host_id: UUID
    integration_key: str
    display_name: str
    adapter_protocol: str
    adapter_version: str
    upstream_version: str | None
    auth_state: str
    health: str
    capabilities: dict
    config_revision: str
    config_options: list
    fetched_at: datetime
    stale_after: datetime
    stale_reason: str | None
    metadata: dict

    model_config = ConfigDict(from_attributes=True)


class AgentHostResponse(BaseModel):
    id: UUID
    user_id: UUID
    organization_id: UUID | None
    installation_id: str
    public_key_fingerprint: str
    display_name: str
    status: AgentHostStatus
    protocol_min: int
    protocol_max: int
    protocol_version: int | None
    host_release: str
    adapter_manifest_id: str
    instance_id: UUID | None
    capacity: dict
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentHostListResponse(BaseModel):
    items: list[AgentHostResponse]


class AgentHostIntegrationListResponse(BaseModel):
    items: list[AgentHostIntegrationResponse]


class AgentHostIntegrationPublishRequest(BaseModel):
    integrations: list[AgentHostIntegrationSnapshot] = Field(
        min_length=1,
        max_length=32,
    )


class AgentHostIntegrationPublishResponse(BaseModel):
    items: list[AgentHostIntegrationResponse]
