"""Right-sized access requests: ask for one resource, not for the pod.

Creating a request is deliberately open to any signed-in user — being unable to
read something is exactly the state you are in when you need to ask about it, so
gating the ask on access would make it unreachable. Deciding one requires the
authority to share the resource.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import (
    PodContextDep,
    reject_delegated_workload_pod,
    require_action,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.resource_actions import RESOURCE_ACTIONS
from app.core.authorization.resource_names import resolve_resource_names_by_ids
from app.core.authorization.service import AuthorizationDataService
from app.modules.pod.api.schemas.pod_schemas import (
    ResourceAccessRequestCreateRequest,
    ResourceAccessRequestListResponse,
    ResourceAccessRequestResponse,
)
from app.modules.pod.domain.pod_entities import ResourceAccessRequestStatus
from app.modules.pod.services.resource_access_request_service import (
    ResourceAccessRequestService,
)

router = APIRouter(
    prefix="/pods/{pod_id}/resource-access-requests",
    tags=["Pod Resource Access Requests"],
)


@router.post(
    "",
    response_model=ResourceAccessRequestResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.resource_access_request.create",
    summary="Request Access to a Resource",
    # A workload must never be able to ask for standing access on a user's
    # behalf; the same reasoning that keeps them from minting membership.
    dependencies=[reject_delegated_workload_pod("request resource access")],
)
async def create_resource_access_request(
    pod_id: UUID,
    data: ResourceAccessRequestCreateRequest,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> ResourceAccessRequestResponse:
    if data.resource_type not in RESOURCE_ACTIONS:
        raise HTTPException(status_code=404, detail="Resource not found")

    resource = await AuthorizationDataService(uow.session).resolve_resource_ref(
        resource_type=data.resource_type,
        pod_id=pod_id,
        resource_id=data.resource_id,
        resource_name=data.resource_name,
    )
    if resource is None or resource.resource_id is None:
        # Same shape as the preview's denial: asking about a name is not a way to
        # learn which names exist.
        raise HTTPException(status_code=404, detail="Resource not found")

    # ``resolve_resource_ref`` returns an id it was handed without checking that
    # anything has it. Without this, any UUID would queue a request against a
    # resource that does not exist, and an owner would be reading a list of them.
    names = await resolve_resource_names_by_ids(
        uow.session,
        pod_id=pod_id,
        refs=[(data.resource_type, resource.resource_id)],
    )
    canonical_name = names.get((data.resource_type, resource.resource_id))
    if canonical_name is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    request = await ResourceAccessRequestService(uow.session).request_access(
        pod_id=pod_id,
        resource_type=data.resource_type,
        resource_id=resource.resource_id,
        resource_name=canonical_name,
        requester_user_id=user.id,
        message=data.message,
    )
    return ResourceAccessRequestResponse.model_validate(request)


@router.get(
    "/me",
    response_model=ResourceAccessRequestResponse | None,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access_request.me",
    summary="Get My Pending Request for a Resource",
)
async def get_my_resource_access_request(
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> ResourceAccessRequestResponse | None:
    request = await ResourceAccessRequestService(uow.session).get_my_request(
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
        requester_user_id=user.id,
    )
    return ResourceAccessRequestResponse.model_validate(request) if request else None


@router.get(
    "",
    response_model=ResourceAccessRequestListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access_request.list",
    summary="List Resource Access Requests",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def list_resource_access_requests(
    pod_id: UUID,
    uow: UoWDep,
    ctx: PodContextDep,
    request_status: ResourceAccessRequestStatus | None = ResourceAccessRequestStatus.PENDING,
) -> ResourceAccessRequestListResponse:
    requests = await ResourceAccessRequestService(uow.session).list_requests(
        pod_id=pod_id,
        status=request_status,
    )
    return ResourceAccessRequestListResponse(
        items=[ResourceAccessRequestResponse.model_validate(item) for item in requests]
    )


@router.post(
    "/{request_id}/approve",
    response_model=ResourceAccessRequestResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access_request.approve",
    summary="Approve a Resource Access Request",
    dependencies=[
        require_action(Permissions.POD_ROLE_MANAGE),
        reject_delegated_workload_pod("approve resource access requests"),
    ],
)
async def approve_resource_access_request(
    pod_id: UUID,
    request_id: UUID,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> ResourceAccessRequestResponse:
    request = await ResourceAccessRequestService(uow.session).approve(
        request_id=request_id,
        pod_id=pod_id,
        decided_by_user_id=user.id,
    )
    return ResourceAccessRequestResponse.model_validate(request)


@router.post(
    "/{request_id}/reject",
    response_model=ResourceAccessRequestResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access_request.reject",
    summary="Reject a Resource Access Request",
    dependencies=[
        require_action(Permissions.POD_ROLE_MANAGE),
        reject_delegated_workload_pod("decide resource access requests"),
    ],
)
async def reject_resource_access_request(
    pod_id: UUID,
    request_id: UUID,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> ResourceAccessRequestResponse:
    request = await ResourceAccessRequestService(uow.session).reject(
        request_id=request_id,
        pod_id=pod_id,
        decided_by_user_id=user.id,
    )
    return ResourceAccessRequestResponse.model_validate(request)
