"""Usage API controller."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.core.api.dependencies import UoWDep
from app.modules.usage.domain.errors import UsageAccessDeniedError
from app.modules.identity.contracts import AuthenticatedUser as UserEntity
from app.modules.usage.api.dependencies import UsageServiceDep
from app.modules.usage.api.schemas import (
    UsageLimitsResponse,
    UsageListResponse,
    UsageQueryParams,
    UsageRecordResponse,
    UsageStatsQueryParams,
    UsageStatsResponse,
    UsageSummaryResponse,
)

router = APIRouter(prefix="/usage", tags=["Usage"], redirect_slashes=False)


def _datetime_range(params: UsageQueryParams) -> tuple[datetime, datetime]:
    end = params.end or datetime.now(timezone.utc)
    start = params.start or (end - timedelta(days=params.days))
    return start, end


def _usage_kind_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _record_response(record) -> UsageRecordResponse:
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
        profile_scope=(
            record.profile_scope.value
            if hasattr(record.profile_scope, "value")
            else str(record.profile_scope)
        ),
        model_name=record.model_name,
        usage_kind=_usage_kind_value(record.usage_kind),
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        total_tokens=record.total_tokens,
        units=record.units,
        cost_usd=record.cost_usd,
        status=record.status,
        metadata=record.metadata,
        occurred_at=record.occurred_at,
        created_at=record.created_at,
    )


def _summary_response(summary) -> UsageSummaryResponse:
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
        total_units=summary.total_units,
        system_cost_usd=summary.system_cost_usd,
        total_by_profile=summary.total_by_profile,
        total_by_model=summary.total_by_model,
        total_by_kind=summary.total_by_kind,
        period_days=summary.period_days,
    )


async def _require_usage_org_access(
    *,
    user: UserEntity,
    organization_id: UUID,
    uow: UoWDep,
) -> None:
    from app.composition.identity_notifications import (
        user_can_view_organization_usage,
    )

    if not await user_can_view_organization_usage(
        uow,
        user_id=user.id,
        organization_id=organization_id,
    ):
        raise UsageAccessDeniedError(
            "Only organization owners and editors can view usage"
        )


@router.get(
    "/organizations/{organization_id}/summary",
    response_model=UsageSummaryResponse,
    status_code=status.HTTP_200_OK,
    operation_id="usage.organization.summary.get",
)
async def get_organization_usage_summary(
    request: Request,
    organization_id: UUID,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: UsageQueryParams = Depends(),
) -> UsageSummaryResponse:
    user: UserEntity = request.state.user
    await _require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = _datetime_range(params)
    summary = await usage_service.get_organization_usage_summary(
        organization_id=organization_id,
        start=start,
        end=end,
        pod_id=params.pod_id,
        user_id=params.user_id,
        agent_id=params.agent_id,
        profile_id=params.profile_id,
        profile_scope=params.profile_scope,
        model_name=params.model_name,
        usage_kind=params.usage_kind,
        source_type=params.source_type,
        status=params.status,
    )
    return _summary_response(summary)


@router.get(
    "/organizations/{organization_id}/events",
    response_model=UsageListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="usage.organization.events.list",
)
async def list_usage_events(
    request: Request,
    organization_id: UUID,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: UsageQueryParams = Depends(),
) -> UsageListResponse:
    user: UserEntity = request.state.user
    await _require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = _datetime_range(params)
    records = await usage_service.get_usage_events(
        organization_id=organization_id,
        start=start,
        end=end,
        pod_id=params.pod_id,
        user_id=params.user_id,
        agent_id=params.agent_id,
        profile_id=params.profile_id,
        profile_scope=params.profile_scope,
        model_name=params.model_name,
        usage_kind=params.usage_kind,
        source_type=params.source_type,
        status=params.status,
        limit=params.limit,
    )
    return UsageListResponse(
        items=[_record_response(record) for record in records],
        total=len(records),
        start_date=start,
        end_date=end,
    )


@router.get(
    "/organizations/{organization_id}/stats",
    response_model=UsageStatsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="usage.organization.stats.get",
)
async def get_usage_stats(
    request: Request,
    organization_id: UUID,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: UsageStatsQueryParams = Depends(),
) -> UsageStatsResponse:
    user: UserEntity = request.state.user
    await _require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = _datetime_range(params)
    rows = await usage_service.get_usage_stats(
        organization_id=organization_id,
        start=start,
        end=end,
        granularity=params.granularity,
        group_by=params.group_by,
        pod_id=params.pod_id,
        user_id=params.user_id,
        agent_id=params.agent_id,
        profile_id=params.profile_id,
        profile_scope=params.profile_scope,
        model_name=params.model_name,
        usage_kind=params.usage_kind,
        source_type=params.source_type,
        status=params.status,
    )
    return UsageStatsResponse(
        items=rows,
        total=len(rows),
        start_date=start,
        end_date=end,
        granularity=params.granularity,
        group_by=params.group_by,
    )


@router.get(
    "/organizations/{organization_id}/limits",
    response_model=UsageLimitsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="usage.organization.limits.get",
)
async def get_usage_limits(
    request: Request,
    organization_id: UUID,
    usage_service: UsageServiceDep,
    uow: UoWDep,
) -> UsageLimitsResponse:
    user: UserEntity = request.state.user
    await _require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    limits = await usage_service.get_usage_limits(
        organization_id=organization_id,
        user_id=user.id,
    )
    return UsageLimitsResponse.model_validate(limits)


@router.get(
    "/organizations/{organization_id}/me",
    response_model=UsageSummaryResponse,
    status_code=status.HTTP_200_OK,
    operation_id="usage.organization.me.summary.get",
)
async def get_my_usage(
    request: Request,
    organization_id: UUID,
    usage_service: UsageServiceDep,
    uow: UoWDep,
    params: UsageQueryParams = Depends(),
) -> UsageSummaryResponse:
    user: UserEntity = request.state.user
    await _require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = _datetime_range(params)
    summary = await usage_service.get_organization_usage_summary(
        organization_id=organization_id,
        start=start,
        end=end,
        user_id=user.id,
        profile_id=params.profile_id,
        profile_scope=params.profile_scope,
        model_name=params.model_name,
        usage_kind=params.usage_kind,
        source_type=params.source_type,
        status=params.status,
    )
    return _summary_response(summary)
