"""Schedule API controller."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.modules.schedule.api.dependencies import (
    ScheduleServiceDep,
    get_current_user_id,
)
from app.modules.schedule.api.schemas.schedule_schemas import (
    CreateScheduleRequest,
    ScheduleDetailResponse,
    ScheduleListResponse,
    UpdateScheduleRequest,
    ScheduleResponse,
    ScheduleRunListResponse,
    ScheduleRunResponse,
)
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleType,
    ScheduleUpdateEntity,
)

router = APIRouter(prefix="/pods/{pod_id}/schedules", tags=["Schedules"])


async def _schedule_detail_response(schedule) -> ScheduleDetailResponse:
    return ScheduleDetailResponse(
        **ScheduleResponse.model_validate(schedule).model_dump(),
        allowed_actions=schedule.allowed_actions,
    )


@router.post(
    "",
    response_model=ScheduleDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="schedule.create",
)
async def create_schedule(
    pod_id: UUID,
    request: CreateScheduleRequest,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
    current_user_id: UUID = Depends(get_current_user_id),
):
    """Create a new pod schedule."""
    await ctx.require(Permissions.SCHEDULE_CREATE, ResourceRef.pod(pod_id))
    schedule_create_data = {
        "user_id": current_user_id,
        "pod_id": pod_id,
        "name": request.name,
        "schedule_type": request.schedule_type,
        "agent_name": request.agent_name,
        "workflow_name": request.workflow_name,
        "config": request.config,
        "account_id": request.account_id,
        "connector_trigger_id": request.connector_trigger_id,
        "filter_instruction": request.filter_instruction,
        "filter_output_schema": request.filter_output_schema,
    }
    if request.visibility is not None:
        schedule_create_data["visibility"] = request.visibility
    schedule_create = ScheduleCreateEntity(**schedule_create_data)
    schedule = await service.create_schedule(schedule_create, ctx=ctx)
    schedule = await service.get_schedule(schedule.id, ctx=ctx) or schedule
    return await _schedule_detail_response(schedule)


@router.get(
    "",
    response_model=ScheduleListResponse,
    operation_id="schedule.list",
)
async def list_schedules(
    pod_id: UUID,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
    schedule_type: Optional[ScheduleType] = None,
    is_active: Optional[bool] = None,
    agent_name: str | None = None,
    workflow_name: str | None = None,
    name: str | None = None,
    limit: int = 100,
    page_token: str | None = None,
) -> ScheduleListResponse:
    """List pod schedules."""
    cursor = parse_uuid_page_token(page_token)
    schedules, next_cursor = await service.list_schedules(
        schedule_type=schedule_type,
        is_active=is_active,
        pod_id=pod_id,
        agent_name=agent_name,
        workflow_name=workflow_name,
        name=name,
        limit=limit,
        cursor=cursor,
        ctx=ctx,
    )
    return ScheduleListResponse(
        items=[ScheduleDetailResponse.model_validate(t) for t in schedules],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/{schedule_id}/runs",
    response_model=ScheduleRunListResponse,
    operation_id="schedule.run.list",
)
async def list_schedule_runs(
    pod_id: UUID,
    schedule_id: UUID,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
    limit: int = 100,
) -> ScheduleRunListResponse:
    runs = await service.list_schedule_runs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        ctx=ctx,
        limit=max(1, min(limit, 1000)),
    )
    if runs is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ScheduleRunListResponse(
        items=[ScheduleRunResponse.model_validate(item) for item in runs],
        limit=max(1, min(limit, 1000)),
    )


@router.post(
    "/{schedule_id}/runs/{run_id}/retry",
    response_model=ScheduleRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="schedule.run.retry",
)
async def retry_schedule_run(
    pod_id: UUID,
    schedule_id: UUID,
    run_id: UUID,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
) -> ScheduleRunResponse:
    schedule_run = await service.retry_schedule_run(
        pod_id=pod_id,
        schedule_id=schedule_id,
        run_id=run_id,
        ctx=ctx,
    )
    if schedule_run is None:
        raise HTTPException(
            status_code=409,
            detail="Schedule run is not failed, dead-lettered, or does not exist",
        )
    return ScheduleRunResponse.model_validate(schedule_run)


@router.get(
    "/{schedule_id}",
    response_model=ScheduleDetailResponse,
    operation_id="schedule.get",
)
async def get_schedule(
    pod_id: UUID,
    schedule_id: UUID,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
):
    """Get a schedule by ID."""
    schedule = await service.get_schedule(schedule_id, ctx=ctx)
    if not schedule or schedule.pod_id != pod_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found"
        )
    await ctx.require(Permissions.SCHEDULE_READ, ResourceRef.schedule(pod_id, schedule_id))

    return await _schedule_detail_response(schedule)


@router.patch(
    "/{schedule_id}",
    response_model=ScheduleDetailResponse,
    operation_id="schedule.update",
)
async def update_schedule(
    pod_id: UUID,
    schedule_id: UUID,
    request: UpdateScheduleRequest,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
):
    """Update a schedule."""
    schedule = await service.get_schedule(schedule_id, ctx=ctx)
    if not schedule or schedule.pod_id != pod_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found"
        )
    await ctx.require(Permissions.SCHEDULE_UPDATE, ResourceRef.schedule(pod_id, schedule_id))

    schedule_update = ScheduleUpdateEntity(
        config=request.config,
        name=request.name,
        agent_name=request.agent_name,
        workflow_name=request.workflow_name,
        filter_instruction=request.filter_instruction,
        filter_output_schema=request.filter_output_schema,
        is_active=request.is_active,
        visibility=request.visibility,
    )

    updated_schedule = await service.update_schedule(schedule_id, schedule_update, ctx=ctx)
    updated_schedule = await service.get_schedule(schedule_id, ctx=ctx) or updated_schedule
    return await _schedule_detail_response(updated_schedule)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="schedule.delete",
)
async def delete_schedule(
    pod_id: UUID,
    schedule_id: UUID,
    service: ScheduleServiceDep,
    ctx: PodContextDep,
):
    """Delete a schedule."""
    schedule = await service.get_schedule(schedule_id)
    if not schedule or schedule.pod_id != pod_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found"
        )
    await ctx.require(Permissions.SCHEDULE_DELETE, ResourceRef.schedule(pod_id, schedule_id))

    await service.delete_schedule(schedule_id)
    return None
