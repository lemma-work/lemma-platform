"""Pod role routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import UoWDep
from app.core.authorization.grants import (
    list_grantee_resource_grants,
    normalize_pod_resource_grants,
    replace_grantee_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.models import RoleModel
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.modules.pod.api.dependencies import PodRoleServiceDep
from app.modules.pod.api.schemas.pod_schemas import (
    PodRoleCreateRequest,
    PodRoleListResponse,
    PodRolePermissionsReplaceRequest,
    PodRolePermissionsResponse,
    PodRoleResponse,
    PodRoleResourcePermissionResponse,
)
from app.modules.pod.domain.visibility import normalize_role_name
from app.modules.pod.infrastructure.models.pod_models import Pod
from sqlalchemy import select

router = APIRouter(prefix="/pods/{pod_id}/roles", tags=["Pod Roles"])


@router.get(
    "",
    response_model=PodRoleListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.roles.list",
    dependencies=[require_action(Permissions.POD_READ)],
)
async def list_pod_roles(
    pod_id: UUID,
    uow: UoWDep,
) -> PodRoleListResponse:
    pod = await uow.session.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found")
    roles = await AuthorizationDataService(uow.session).list_roles(
        organization_id=pod.organization_id,
        pod_id=pod_id,
    )
    return PodRoleListResponse(
        items=[
            PodRoleResponse(
                id=role.id,
                organization_id=role.organization_id,
                pod_id=pod_id,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                created_by_user_id=role.created_by_user_id,
                created_at=role.created_at,
                permission_ids=list(role.permission_ids),
            )
            for role in roles
        ]
    )


@router.post(
    "",
    response_model=PodRoleResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.roles.create",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def create_pod_role(
    pod_id: UUID,
    data: PodRoleCreateRequest,
    ctx: PodContextDep,
    role_service: PodRoleServiceDep,
    uow: UoWDep,
) -> PodRoleResponse:
    if ctx.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user expected",
        )
    pod = await uow.session.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found")
    try:
        await role_service.create_role(
            pod_id=pod_id,
            name=data.name,
            created_by_user_id=ctx.user_id,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
    try:
        role = await AuthorizationDataService(uow.session).create_or_update_role(
            organization_id=pod.organization_id,
            pod_id=pod_id,
            name=data.name,
            description=data.description,
            permission_ids=data.permission_ids,
            created_by_user_id=ctx.user_id,
        )
    except ValueError as exc:
        # A typo in a permission id is a mistake in the request, not a fault in
        # the platform -- the message already names what was wrong.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PodRoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        pod_id=pod_id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        created_by_user_id=role.created_by_user_id,
        created_at=role.created_at,
        permission_ids=list(role.permission_ids),
    )


@router.patch(
    "/{role_name}",
    response_model=PodRoleResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.roles.update",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def update_pod_role(
    pod_id: UUID,
    role_name: str,
    data: PodRoleCreateRequest,
    ctx: PodContextDep,
    uow: UoWDep,
) -> PodRoleResponse:
    if ctx.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user expected",
        )
    pod = await uow.session.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found")
    try:
        role = await AuthorizationDataService(uow.session).create_or_update_role(
            organization_id=pod.organization_id,
            pod_id=pod_id,
            name=role_name,
            description=data.description,
            permission_ids=data.permission_ids,
            created_by_user_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PodRoleResponse(
        id=role.id,
        organization_id=role.organization_id,
        pod_id=pod_id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        created_by_user_id=role.created_by_user_id,
        created_at=role.created_at,
        permission_ids=list(role.permission_ids),
    )


@router.delete(
    "/{role_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.roles.delete",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def delete_pod_role(
    pod_id: UUID,
    role_name: str,
    role_service: PodRoleServiceDep,
    uow: UoWDep,
) -> None:
    await role_service.delete_role(pod_id=pod_id, role_name=role_name)
    pod = await uow.session.get(Pod, pod_id)
    if pod is not None:
        await AuthorizationDataService(uow.session).delete_role(
            organization_id=pod.organization_id,
            pod_id=pod_id,
            name=role_name,
        )


@router.get(
    "/{role_name}/permissions",
    response_model=PodRolePermissionsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.role.permissions.get",
    dependencies=[require_action(Permissions.POD_READ)],
)
async def get_pod_role_permissions(
    pod_id: UUID,
    role_name: str,
    uow: UoWDep,
) -> PodRolePermissionsResponse:
    role = await _get_core_pod_role(uow, pod_id=pod_id, role_name=role_name)
    return await _role_permissions_response(uow, pod_id=pod_id, role=role)


@router.put(
    "/{role_name}/permissions",
    response_model=PodRolePermissionsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.role.permissions.replace",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def replace_pod_role_permissions(
    pod_id: UUID,
    role_name: str,
    data: PodRolePermissionsReplaceRequest,
    ctx: PodContextDep,
    uow: UoWDep,
) -> PodRolePermissionsResponse:
    role = await _get_core_pod_role(uow, pod_id=pod_id, role_name=role_name)
    validate_pod_resource_grant_permissions(data.grants)
    grants = await normalize_pod_resource_grants(
        uow.session,
        pod_id=pod_id,
        grants=data.grants,
    )
    await replace_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="ROLE",
        grantee_id=role.id,
        grants=grants,
        created_by_user_id=ctx.user_id,
    )
    return await _role_permissions_response(uow, pod_id=pod_id, role=role)


async def _get_core_pod_role(
    uow: UoWDep,
    *,
    pod_id: UUID,
    role_name: str,
) -> RoleModel:
    pod = await uow.session.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found")
    await AuthorizationDataService(uow.session).ensure_pod_system_roles(
        organization_id=pod.organization_id,
        pod_id=pod_id,
    )
    stmt = select(RoleModel).where(
        RoleModel.organization_id == pod.organization_id,
        RoleModel.pod_id == pod_id,
        RoleModel.name == normalize_role_name(role_name),
    )
    role = (await uow.session.execute(stmt)).scalars().first()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _role_permissions_response(
    uow: UoWDep,
    *,
    pod_id: UUID,
    role: RoleModel,
) -> PodRolePermissionsResponse:
    grouped = await list_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="ROLE",
        grantee_id=role.id,
    )
    return PodRolePermissionsResponse(
        role_id=role.id,
        role_name=role.name,
        grants=[
            PodRoleResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grouped.items()
        ],
    )
