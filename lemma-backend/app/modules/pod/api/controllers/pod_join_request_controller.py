from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.service import AuthorizationDataService
from app.modules.identity.domain.organization_entities import OrganizationRole
from app.modules.pod.api.dependencies import PodJoinRequestServiceDep
from app.modules.pod.api.schemas.pod_schemas import (
    PodJoinRequestApproveRequest,
    PodJoinRequestCreateResponse,
    PodJoinRequestListResponse,
)
from app.modules.pod.domain.pod_entities import PodJoinRequestStatus

router = APIRouter(prefix="/pods/{pod_id}/join-requests", tags=["Pod Join Requests"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.join_request.create",
    summary="Create Pod Join Request",
    description="Create a join request for the current user to access this pod",
    response_model=PodJoinRequestCreateResponse,
)
async def create_join_request(
    pod_id: UUID,
    pod_join_request_service: PodJoinRequestServiceDep,
    user: CurrentUser,
    uow: UoWDep,
) -> PodJoinRequestCreateResponse:
    join_request, created_org_member = await pod_join_request_service.request_join(
        pod_id, user.id
    )
    if created_org_member is not None:
        await AuthorizationDataService(uow.session).assign_roles(
            organization_id=created_org_member.organization_id,
            pod_id=None,
            principal_type="ORG_MEMBER",
            principal_id=created_org_member.id,
            role_names=[OrganizationRole.ORG_MEMBER.value],
            assigned_by_user_id=user.id,
        )
    return PodJoinRequestCreateResponse.model_validate(join_request)


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    operation_id="pod.join_request.me",
    summary="Get My Pod Join Request",
    description="Get the current user's pending join request for this pod",
    response_model=PodJoinRequestCreateResponse | None,
)
async def get_my_join_request(
    pod_id: UUID,
    pod_join_request_service: PodJoinRequestServiceDep,
    user: CurrentUser,
) -> PodJoinRequestCreateResponse | None:
    join_request = await pod_join_request_service.get_my_join_request(pod_id, user.id)
    return (
        PodJoinRequestCreateResponse.model_validate(join_request)
        if join_request
        else None
    )


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    operation_id="pod.join_request.list",
    summary="List Pod Join Requests",
    description="List join requests for a pod",
    response_model=PodJoinRequestListResponse,
)
async def list_join_requests(
    pod_id: UUID,
    pod_join_request_service: PodJoinRequestServiceDep,
    user: CurrentUser,
    status_filter: PodJoinRequestStatus | None = PodJoinRequestStatus.PENDING,
    limit: int = 100,
    page_token: str | None = None,
) -> PodJoinRequestListResponse:
    parse_uuid_page_token(page_token)

    requests, next_cursor = await pod_join_request_service.list_join_requests(
        pod_id,
        user.id,
        status=status_filter,
        limit=limit,
        cursor=page_token,
    )

    return PodJoinRequestListResponse(
        items=[PodJoinRequestCreateResponse.model_validate(item) for item in requests],
        limit=limit,
        total=len(requests),
        next_page_token=next_cursor,
    )


@router.post(
    "/{join_request_id}/approve",
    status_code=status.HTTP_200_OK,
    operation_id="pod.join_request.approve",
    summary="Approve Pod Join Request",
    description="Approve a pending pod join request and add user to org/pod",
    response_model=PodJoinRequestCreateResponse,
)
async def approve_join_request(
    pod_id: UUID,
    join_request_id: UUID,
    data: PodJoinRequestApproveRequest,
    pod_join_request_service: PodJoinRequestServiceDep,
    user: CurrentUser,
) -> PodJoinRequestCreateResponse:
    join_request = await pod_join_request_service.approve_join_request(
        pod_id,
        join_request_id,
        user.id,
        org_role=data.org_role,
        pod_role=data.pod_role,
    )
    return PodJoinRequestCreateResponse.model_validate(join_request)
