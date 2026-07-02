from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, status

from app.core.api.pagination import parse_uuid_page_token
from app.core.api.dependencies import CurrentUser
from app.core.authorization.dependencies import pod_from_path, require_action
from app.core.authorization.permissions import Permissions
from app.modules.pod.api.dependencies import PodMemberServiceDep
from app.modules.pod.api.schemas.pod_schemas import (
    PodMemberAddRequest,
    PodMemberUpdateRoleRequest,
    PodMemberResponse,
    PodMemberDetailResponse,
    PodMemberListResponse,
)
from app.modules.pod.domain.pod_entities import PodMemberEntity

router = APIRouter(prefix="/pods/{pod_id}/members", tags=["Pod Members"])

# Managing pod members is destructive: routing through require_action puts it
# behind ctx.require, so a delegated workload is gated (needs an explicit grant
# or a session approval) instead of inheriting its user's admin power silently.
# A human admin (holding pod.member.manage) is unaffected.
_PodMemberManageDep = require_action(Permissions.POD_MEMBER_MANAGE, pod_from_path)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.member.add",
    summary="Add Pod Member",
    description="Add a member to a pod",
    response_model=PodMemberResponse,
)
async def add_member(
    pod_id: UUID,
    data: PodMemberAddRequest,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
) -> PodMemberResponse:
    """Add a member to a pod."""
    entity = PodMemberEntity(
        pod_id=pod_id,
        organization_member_id=data.organization_member_id,
        roles=data.roles,
    )

    member = await pod_member_service.assign_member_to_pod(entity, user.id)
    return PodMemberResponse.model_validate(member)


@router.get(
    "/lookup/by-user-id/{user_id}",
    status_code=status.HTTP_200_OK,
    operation_id="pod.member.lookup_by_user_id",
    summary="Lookup Pod Member By User ID",
    description="Resolve a pod member by user id",
    response_model=PodMemberDetailResponse,
)
async def lookup_member_by_user_id(
    pod_id: UUID,
    user_id: UUID,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
) -> PodMemberDetailResponse:
    """Resolve pod member details by user id."""
    member = await pod_member_service.get_pod_member_by_user_id(
        pod_id,
        user_id,
        user.id,
    )
    return PodMemberDetailResponse.model_validate(member)


@router.get(
    "/lookup/by-email",
    status_code=status.HTTP_200_OK,
    operation_id="pod.member.lookup_by_email",
    summary="Lookup Pod Member By Email",
    description="Resolve a pod member by email",
    response_model=PodMemberDetailResponse,
)
async def lookup_member_by_email(
    pod_id: UUID,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
    email: str = Query(..., min_length=3),
) -> PodMemberDetailResponse:
    """Resolve pod member details by email."""
    member = await pod_member_service.get_pod_member_by_user_email(
        pod_id,
        email,
        user.id,
    )
    return PodMemberDetailResponse.model_validate(member)


@router.get(
    "/{pod_member_id}",
    status_code=status.HTTP_200_OK,
    operation_id="pod.member.get",
    summary="Get Pod Member",
    description="Get a pod member by pod member id",
    response_model=PodMemberDetailResponse,
)
async def get_member(
    pod_id: UUID,
    pod_member_id: UUID,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
) -> PodMemberDetailResponse:
    """Get pod member details by pod member id."""
    member = await pod_member_service.get_pod_member_by_id(
        pod_id,
        pod_member_id,
        user.id,
    )
    return PodMemberDetailResponse.model_validate(member)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    operation_id="pod.member.list",
    summary="List Pod Members",
    description="List all members of a pod",
    response_model=PodMemberListResponse,
)
async def list_members(
    pod_id: UUID,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
    limit: int = 100,
    page_token: str | None = None,
) -> PodMemberListResponse:
    """List all members of a pod."""
    parse_uuid_page_token(page_token)

    members, next_cursor = await pod_member_service.list_pod_members(
        pod_id, user.id, limit, page_token
    )

    return PodMemberListResponse(
        items=[PodMemberResponse.model_validate(m) for m in members],
        limit=limit,
        total=len(members),
        next_page_token=next_cursor,
    )


@router.patch(
    "/{pod_member_id}/roles",
    status_code=status.HTTP_200_OK,
    operation_id="pod.member.update_roles",
    summary="Update Member Roles",
    description="Update a pod member's roles",
    response_model=PodMemberResponse,
    dependencies=[_PodMemberManageDep],
)
async def update_member_roles(
    pod_id: UUID,
    pod_member_id: UUID,
    data: PodMemberUpdateRoleRequest,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
) -> PodMemberResponse:
    target_roles = data.roles
    if not target_roles:
        raise HTTPException(status_code=422, detail="roles is required")
    member = await pod_member_service.update_member_roles(
        pod_id,
        pod_member_id,
        target_roles,
        user.id,
    )
    return PodMemberResponse.model_validate(member)


@router.delete(
    "/{pod_member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.member.remove",
    summary="Remove Pod Member",
    description="Remove a member from a pod",
    dependencies=[_PodMemberManageDep],
)
async def remove_member(
    pod_id: UUID,
    pod_member_id: UUID,
    pod_member_service: PodMemberServiceDep,
    user: CurrentUser,
) -> None:
    """Remove a member from a pod."""
    await pod_member_service.remove_member_from_pod(
        pod_id,
        pod_member_id,
        user.id,
    )
