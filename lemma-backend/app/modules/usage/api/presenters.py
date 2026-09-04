"""Turning a usage entity into the shape the API promises.

Separated from the controller because the mapping is the part that changes when
a column is added -- three of them arrived with the cached/uncached split -- and
a controller is easier to read when it is routes and authorization rather than
forty lines of field copying.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.usage.api.schemas import (
    UsageQueryParams,
    UsageRecordResponse,
    UsageSummaryResponse,
)
from app.modules.usage.domain.entities import UsageRecord, UsageSummary


def datetime_range(params: UsageQueryParams) -> tuple[datetime, datetime]:
    """The window a request asked for, as two instants.

    ``days`` is the fallback rather than the primary: an explicit ``start`` and
    ``end`` win, so a caller paging through a fixed window gets the same window
    every time rather than one that slides under them.
    """
    end = params.end or datetime.now(timezone.utc)
    start = params.start or (end - timedelta(days=params.days))
    return start, end


def enum_value(value: object) -> str:
    """The wire form of a field that may arrive as an enum or as a string.

    Both shapes really do reach here: the ORM reads a plain column, while a
    freshly built entity still holds the enum it was constructed with.
    """
    return value.value if hasattr(value, "value") else str(value)


def record_response(record: UsageRecord) -> UsageRecordResponse:
    return UsageRecordResponse(
        id=record.id,
        organization_id=record.organization_id,
        pod_id=record.pod_id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        conversation_id=record.conversation_id,
        agent_run_id=record.agent_run_id,
        parent_agent_run_id=record.parent_agent_run_id,
        source_type=record.source_type,
        source_id=record.source_id,
        profile_id=record.profile_id,
        profile_scope=enum_value(record.profile_scope),
        model_name=record.model_name,
        usage_kind=enum_value(record.usage_kind),
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        total_tokens=record.total_tokens,
        cached_input_tokens=record.cached_input_tokens,
        cache_write_tokens=record.cache_write_tokens,
        uncached_input_tokens=record.uncached_input_tokens,
        units=record.units,
        cost_usd=record.cost_usd,
        cost_source=enum_value(record.cost_source),
        status=record.status,
        metadata=record.metadata,
        occurred_at=record.occurred_at,
        created_at=record.created_at,
    )


def summary_response(summary: UsageSummary) -> UsageSummaryResponse:
    return UsageSummaryResponse(
        organization_id=summary.organization_id,
        pod_id=summary.pod_id,
        user_id=summary.user_id,
        agent_id=summary.agent_id,
        start_date=summary.start_date,
        end_date=summary.end_date,
        total_input_tokens=summary.total_input_tokens,
        total_output_tokens=summary.total_output_tokens,
        total_tokens=summary.total_tokens,
        total_cached_input_tokens=summary.total_cached_input_tokens,
        total_cache_write_tokens=summary.total_cache_write_tokens,
        total_uncached_input_tokens=summary.total_uncached_input_tokens,
        total_units=summary.total_units,
        system_cost_usd=summary.system_cost_usd,
        total_cost_usd=summary.total_cost_usd,
        total_by_profile=summary.total_by_profile,
        total_by_model=summary.total_by_model,
        total_by_kind=summary.total_by_kind,
        period_days=summary.period_days,
    )
