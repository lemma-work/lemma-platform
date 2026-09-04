"""Usage API controller."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.core.api.dependencies import UoWDep
from app.modules.identity.contracts import AuthenticatedUser as UserEntity
from app.modules.usage.api.authorization import (
    require_usage_org_access,
    require_usage_org_membership,
)
from app.modules.usage.api.dependencies import UsageServiceDep
from app.modules.usage.api.presenters import (
    datetime_range,
    record_response,
    summary_response,
)
from app.modules.usage.api.schemas import (
    UsageLimitsResponse,
    UsageListResponse,
    UsageQueryParams,
    UsageStatsQueryParams,
    UsageStatsResponse,
    UsageSummaryResponse,
)

router = APIRouter(prefix="/usage", tags=["Usage"], redirect_slashes=False)


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
    await require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = datetime_range(params)
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
    return summary_response(summary)


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
    await require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = datetime_range(params)
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
        items=[record_response(record) for record in records],
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
    await require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
    start, end = datetime_range(params)
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
    await require_usage_org_access(user=user, organization_id=organization_id, uow=uow)
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
    await require_usage_org_membership(
        user=user, organization_id=organization_id, uow=uow
    )
    start, end = datetime_range(params)
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
    return summary_response(summary)
