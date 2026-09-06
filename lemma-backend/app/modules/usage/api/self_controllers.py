"""Member-safe views of the plan funding the current context."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.api.dependencies import UoWDep
from app.modules.identity.contracts.organizations import organization_member_role
from app.modules.usage.api.controllers import _record_response, _summary_response
from app.modules.usage.api.dependencies import UsageServiceDep
from app.modules.usage.api.schemas import (
    MyUsageLimitsResponse,
    MyUsageQueryParams,
    UsageAllowanceResponse,
    UsageListResponse,
    UsageStatsBucketResponse,
    UsageStatsResponse,
    UsageSummaryResponse,
)
from app.modules.usage.config import usage_settings
from app.modules.usage.domain.errors import UsageAccessDeniedError
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.domain.query_types import UsageReportQuery

router = APIRouter(prefix="/usage/me", tags=["Usage"], redirect_slashes=False)


async def _resolve_plan(
    request: Request,
    organization_id: UUID | None,
    usage_service: UsageServiceDep,
    uow: UoWDep,
) -> UsageLimitValues:
    if organization_id is not None:
        role = await organization_member_role(
            uow, user_id=request.state.user.id, organization_id=organization_id
        )
        if role is None:
            raise UsageAccessDeniedError()
    return await usage_service.resolve_usage_limit_values(
        organization_id=organization_id, user_id=request.state.user.id
    )


def _report_query(
    params: MyUsageQueryParams, user_id: UUID, values: UsageLimitValues
) -> tuple[UUID | None, UsageReportQuery]:
    end = params.end or datetime.now(timezone.utc)
    start = params.start or end - timedelta(days=params.days)
    global_scope = values.user_limit_scope == "global"
    return (None if global_scope else params.organization_id), UsageReportQuery(
        start=start,
        end=end,
        user_id=user_id,
        exclude_organization_ids=values.excluded_organization_ids
        if global_scope
        else (),
        agent_run_id=params.agent_run_id,
        conversation_id=params.conversation_id,
    )


@router.get(
    "/limits", response_model=MyUsageLimitsResponse, operation_id="usage.me.limits.get"
)
async def my_limits(
    request: Request,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    organization_id: UUID | None = None,
) -> MyUsageLimitsResponse:
    values = await _resolve_plan(request, organization_id, usage_service, uow)
    limits = await usage_service.get_usage_limits(
        organization_id=organization_id,
        user_id=request.state.user.id,
        _limit_values=values,
    )
    windows: list[UsageAllowanceResponse] = []
    for key, label, scope in (
        ("user_weekly", "Your weekly allowance", limits["user_weekly"]),
        ("user_monthly", "Your monthly allowance", limits["user_monthly"]),
        ("org_monthly", "Organization monthly allowance", limits["org_monthly"]),
    ):
        cap = scope["limit_usd"]
        if cap is None or (key == "org_monthly" and organization_id is None):
            continue
        consumed = scope["used_usd"] + scope["reserved_usd"]
        windows.append(
            UsageAllowanceResponse(
                key=key,
                label=label,
                used_percent=100 * consumed / cap if cap > 0 else 100,
                allowed=scope["allowed"],
                reset_at=scope["reset_at"],
            )
        )
    return MyUsageLimitsResponse(
        organization_id=organization_id,
        payer=values.payer,
        plan_name=values.plan_name,
        windows=windows,
        allowed=all(window.allowed for window in windows),
        warning_percent=100 * usage_settings.usage_limit_warn_fraction,
    )


@router.get(
    "/summary", response_model=UsageSummaryResponse, operation_id="usage.me.summary.get"
)
async def my_summary(
    request: Request,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: MyUsageQueryParams = Depends(),
) -> UsageSummaryResponse:
    values = await _resolve_plan(request, params.organization_id, usage_service, uow)
    organization_id, query = _report_query(params, request.state.user.id, values)
    summary = await usage_service.get_organization_usage_summary(
        organization_id=organization_id, **query
    )
    return _summary_response(summary)


@router.get(
    "/events", response_model=UsageListResponse, operation_id="usage.me.events.list"
)
async def my_events(
    request: Request,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: MyUsageQueryParams = Depends(),
) -> UsageListResponse:
    values = await _resolve_plan(request, params.organization_id, usage_service, uow)
    organization_id, query = _report_query(params, request.state.user.id, values)
    records = await usage_service.get_usage_events(
        organization_id=organization_id, limit=params.limit, **query
    )
    return UsageListResponse(
        items=[_record_response(record) for record in records],
        total=len(records),
        start_date=query["start"],
        end_date=query["end"],
    )


@router.get(
    "/stats", response_model=UsageStatsResponse, operation_id="usage.me.stats.get"
)
async def my_stats(
    request: Request,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: MyUsageQueryParams = Depends(),
) -> UsageStatsResponse:
    values = await _resolve_plan(request, params.organization_id, usage_service, uow)
    organization_id, query = _report_query(params, request.state.user.id, values)
    rows = await usage_service.get_usage_stats(organization_id=organization_id, **query)
    return UsageStatsResponse(
        items=[UsageStatsBucketResponse.model_validate(row) for row in rows],
        total=len(rows),
        start_date=query["start"],
        end_date=query["end"],
        granularity="day",
        group_by=None,
    )
