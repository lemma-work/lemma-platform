"""Usage API schemas."""

from __future__ import annotations

from datetime import datetime
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
    # `input_tokens` is the inclusive total the provider reported. The two cache
    # buckets are subsets of it, and `uncached_input_tokens` is the remainder --
    # the part billed at the full rate. Without the split, a heavily cached run
    # and an uncached one of the same size look identical and cost 10x apart.
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    uncached_input_tokens: int = 0
    units: float
    cost_usd: float | None = None
    # Which pricing layer produced `cost_usd`: REGISTERED (a rate this
    # deployment configured), ESTIMATED (a public dataset), UNKNOWN (nothing
    # could price it, and `cost_usd` is null rather than zero).
    cost_source: str = "UNKNOWN"
    status: str | None = None
    metadata: dict[str, object]
    occurred_at: datetime
    created_at: datetime


class UsageSummaryResponse(BaseModel):
    organization_id: UUID | None = None
    pod_id: UUID | None = None
    user_id: UUID | None = None
    agent_id: UUID | None = None
    start_date: datetime
    end_date: datetime
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cached_input_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_uncached_input_tokens: int = 0
    total_units: float
    # Spend on this deployment's credentials -- what a plan limit is measured
    # against. `total_cost_usd` additionally includes runtime profiles someone
    # added with their own key, which bill their provider rather than Lemma.
    system_cost_usd: float
    total_cost_usd: float = 0.0
    total_by_profile: dict[str, dict[str, object]]
    total_by_model: dict[str, dict[str, object]]
    total_by_kind: dict[str, dict[str, object]]
    period_days: int


class UsageQueryParams(BaseModel):
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
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    uncached_input_tokens: int = 0
    units: float
    system_cost_usd: float
    total_cost_usd: float = 0.0


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
