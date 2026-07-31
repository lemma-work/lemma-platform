from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, HTTPException, status

from app.core.api.pagination import parse_uuid_page_token
from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.service import AuthorizationDataService
from app.modules.identity.contracts import OrganizationRole
from app.modules.pod.api.dependencies import (
    PodServiceDep,
    PodJoinRequestServiceDep,
    PodViewerDep,
    PodEditorDep,
    PodAdminDep,
)
from app.modules.pod.api.schemas.pod_schemas import (
    PodCreateRequest,
    PodUpdateRequest,
    PodResponse,
    PodListResponse,
    PodMemberResponse,
)
from app.modules.pod.domain.pod_entities import (
    PodEntity,
    PodUpdateEntity,
)

router = APIRouter(prefix="/pods", tags=["Pods"], redirect_slashes=False)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.create",
    summary="Create Pod",
    description="Create a new pod",
    response_model=PodResponse,
)
async def create_pod(
    data: PodCreateRequest,
    pod_service: PodServiceDep,
    user: CurrentUser,
) -> PodResponse:
    """Create a new pod."""
    entity = PodEntity(
        user_id=user.id,
        organization_id=data.organization_id,
        name=data.name,
        description=data.description,
        icon_url=data.icon_url,
        config=data.config,
    )

    pod = await pod_service.create_pod(entity, user.id)
    return PodResponse.model_validate(pod)


@router.get(
    "/{pod_id}",
    dependencies=[PodViewerDep],
    status_code=status.HTTP_200_OK,
    operation_id="pod.get",
    summary="Get Pod",
    description="Get pod details",
    response_model=PodResponse,
)
async def get_pod(
    pod_id: UUID,
    pod_service: PodServiceDep,
    user: CurrentUser,
) -> PodResponse:
    """Get pod details."""
    pod = await pod_service.get_pod(pod_id, user.id)
    if not pod:
        # This might actually be handled by the dependency too, but keeping it for safety
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found"
        )

    return PodResponse.model_validate(pod)


@router.put(
    "/{pod_id}",
    dependencies=[PodEditorDep],
    status_code=status.HTTP_200_OK,
    operation_id="pod.update",
    summary="Update Pod",
    description="Update pod details",
    response_model=PodResponse,
)
async def update_pod(
    pod_id: UUID,
    data: PodUpdateRequest,
    pod_service: PodServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> PodResponse:
    """Update pod details."""
    entity = PodUpdateEntity(**data.model_dump(exclude_unset=True))

    pod = await pod_service.update_pod(pod_id, entity, user.id, ctx=ctx)
    return PodResponse.model_validate(pod)


@router.delete(
    "/{pod_id}",
    dependencies=[PodAdminDep],
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.delete",
    summary="Delete Pod",
    description="Delete a pod",
)
async def delete_pod(
    pod_id: UUID,
    pod_service: PodServiceDep,
    user: CurrentUser,
) -> None:
    """Delete a pod."""
    await pod_service.delete_pod(pod_id, user.id)


@router.get(
    "/organization/{organization_id}",
    status_code=status.HTTP_200_OK,
    operation_id="pod.list",
    summary="List PodS by Organization",
    description="List all pods in an organization",
    response_model=PodListResponse,
)
async def list_pods_by_organization(
    organization_id: UUID,
    pod_service: PodServiceDep,
    user: CurrentUser,
    limit: int = 100,
    page_token: str | None = None,
) -> PodListResponse:
    """List all pods in an organization."""
    parse_uuid_page_token(page_token)

    pods, cursor = await pod_service.list_pods_by_organization(
        organization_id, user.id, limit, page_token
    )

    return PodListResponse(
        items=[PodResponse.model_validate(pod) for pod in pods],
        limit=limit,
        total=len(pods),
        next_page_token=cursor,
    )


@router.post(
    "/{pod_id}/join",
    status_code=status.HTTP_200_OK,
    operation_id="pod.join",
    summary="Join Pod",
    description="Self-join a pod when its join policy (ORG_MEMBERS / PUBLIC) allows it",
    response_model=PodMemberResponse,
)
async def join_pod(
    pod_id: UUID,
    pod_join_request_service: PodJoinRequestServiceDep,
    user: CurrentUser,
    uow: UoWDep,
) -> PodMemberResponse:
    """Self-join a pod based on its join policy."""
    pod_member, created_org_member = await pod_join_request_service.join_pod(
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
    return PodMemberResponse.model_validate(pod_member)
