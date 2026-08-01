"""HTTP response/request shapes for Agent Host management APIs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.agent_host import (
    AgentHostHarnessSnapshot,
    AgentHostStatus,
)


class AgentHostHarnessResponse(BaseModel):
    id: UUID
    host_id: UUID
    harness_key: str
    display_name: str
    adapter_version: str
    upstream_version: str | None
    health: str
    capabilities: dict
    config_revision: str
    config_options: list
    stale_after: datetime
    stale_reason: str | None

    model_config = ConfigDict(from_attributes=True)


class AgentHostResponse(BaseModel):
    id: UUID
    user_id: UUID
    installation_id: str
    display_name: str
    status: AgentHostStatus
    protocol_version: int | None
    host_release: str
    capacity: dict
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentHostListResponse(BaseModel):
    items: list[AgentHostResponse]


class AgentHostHarnessListResponse(BaseModel):
    items: list[AgentHostHarnessResponse]


class AgentHostHarnessPublishRequest(BaseModel):
    harnesses: list[AgentHostHarnessSnapshot] = Field(
        min_length=1,
        max_length=32,
    )


class AgentHostHarnessPublishResponse(BaseModel):
    items: list[AgentHostHarnessResponse]
