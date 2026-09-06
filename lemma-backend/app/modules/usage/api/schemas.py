"""Usage API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UsageRecordResponse(BaseModel):
    id: UUID
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    parent_agent_run_id: UUID | None = None
    source_type: str
    source_id: str | None = None
    profile_id: str
    profile_scope: str
    model_name: str
    usage_kind: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    units: float
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    status: str | None = None
    metadata: dict[str, object]
    occurred_at: datetime
    created_at: datetime


class UsageSummaryResponse(BaseModel):
    agent_run_id: UUID | None = None
    conversation_id: UUID | None = None
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID | None = None
    agent_id: UUID | None = None
    start_date: datetime
    end_date: datetime
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_units: float
    system_cost_usd: float
    total_by_profile: dict[str, dict[str, object]]
    total_by_model: dict[str, dict[str, object]]
    total_by_kind: dict[str, dict[str, object]]
    period_days: int


class UsageQueryParams(BaseModel):
    agent_run_id: UUID | None = None
    conversation_id: UUID | None = None
    start: datetime | None = Field(default=None)
    end: datetime | None = Field(default=None)
    days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=100, ge=1, le=1000)
    pod_id: UUID | None = Field(default=None)
    user_id: UUID | None = Field(default=None)
    agent_id: UUID | None = Field(default=None)
    profile_id: str | None = Field(default=None)
    profile_scope: str | None = Field(default=None)
    model_name: str | None = Field(default=None)
    usage_kind: str | None = Field(default=None)
    source_type: str | None = Field(default=None)
    status: str | None = Field(default=None)


class UsageListResponse(BaseModel):
    items: list[UsageRecordResponse]
    total: int
    start_date: datetime
    end_date: datetime


class UsageStatsQueryParams(UsageQueryParams):
    granularity: str = Field(default="day", pattern="^(hour|day|week)$")
    group_by: str | None = Field(
        default=None,
        pattern="^(profile|model|user|pod|agent|kind|source)$",
    )


class UsageStatsBucketResponse(BaseModel):
    bucket: datetime
    group: str | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    units: float
    system_cost_usd: float


class UsageStatsResponse(BaseModel):
    items: list[UsageStatsBucketResponse]
    total: int
    start_date: datetime
    end_date: datetime
    granularity: str
    group_by: str | None = None


class UsageLimitScopeResponse(BaseModel):
    limit_usd: float | None = None
    scope: str
    used_usd: float
    reserved_usd: float
    remaining_usd: float | None = None
    allowed: bool
    reset_at: datetime
    window_start: datetime


class UsageLimitsResponse(BaseModel):
    organization_id: UUID | None
    user_id: UUID
    org_monthly: UsageLimitScopeResponse
    user_weekly: UsageLimitScopeResponse
    user_monthly: UsageLimitScopeResponse
    allowed: bool


class UsageAllowanceResponse(BaseModel):
    key: str
    label: str
    used_percent: float
    allowed: bool
    reset_at: datetime


class MyUsageLimitsResponse(BaseModel):
    organization_id: UUID | None
    plan_type: Literal["PERSONAL", "TEAM"] | None
    plan_name: str | None
    windows: list[UsageAllowanceResponse]
    allowed: bool
    warning_percent: float


class MyUsageQueryParams(BaseModel):
    organization_id: UUID | None = None
    start: datetime | None = None
    end: datetime | None = None
    days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=50, ge=1, le=1000)
    agent_run_id: UUID | None = None
    conversation_id: UUID | None = None
