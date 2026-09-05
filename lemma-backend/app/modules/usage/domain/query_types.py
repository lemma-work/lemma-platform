"""Named shapes shared by usage queries and their readers."""

from datetime import datetime
from typing import NotRequired, TypedDict
from uuid import UUID


class UsageFilters(TypedDict, total=False):
    organization_id: UUID | None
    pod_id: UUID | None
    user_id: UUID | None
    agent_id: UUID | None
    agent_run_id: UUID | None
    conversation_id: UUID | None
    profile_id: str | None
    profile_scope: str | None
    model_name: str | None
    usage_kind: str | None
    source_type: str | None
    status: str | None


class UsageStatsQuery(TypedDict):
    start: datetime
    end: datetime
    granularity: NotRequired[str]
    group_by: NotRequired[str | None]
    pod_id: NotRequired[UUID | None]
    user_id: NotRequired[UUID | None]
    agent_id: NotRequired[UUID | None]
    agent_run_id: NotRequired[UUID | None]
    conversation_id: NotRequired[UUID | None]
    profile_id: NotRequired[str | None]
    profile_scope: NotRequired[str | None]
    model_name: NotRequired[str | None]
    usage_kind: NotRequired[str | None]
    source_type: NotRequired[str | None]
    status: NotRequired[str | None]


class UsageStatsBucket(TypedDict):
    bucket: datetime
    group: NotRequired[str | None]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    units: float
    system_cost_usd: float


class UsageLimitScope(TypedDict):
    limit_usd: float | None
    scope: str
    used_usd: float
    reserved_usd: float
    remaining_usd: float | None
    allowed: bool
    reset_at: datetime
    window_start: datetime
    counter_organization_id: UUID | None


class UsageLimits(TypedDict):
    organization_id: UUID | None
    user_id: UUID
    org_monthly: UsageLimitScope
    user_weekly: UsageLimitScope
    user_monthly: UsageLimitScope
    allowed: bool
