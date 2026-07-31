"""Resource-centric access grant routes (resources addressed by name)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.api.dependencies import UoWDep
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.grants import (
    delete_resource_grantee_grant,
    list_resource_grants,
    replace_resource_grantee_grant,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.models import RoleModel
from app.core.authorization.permissions import Permissions
from app.core.authorization.resource_actions import RESOURCE_ACTIONS
from app.core.authorization.resource_names import resolve_resource_id_by_name
from app.core.authorization.service import AuthorizationDataService
from app.composition.pod_identity_wiring import OrganizationMember, User
from app.modules.pod.api.schemas.pod_schemas import (
    ResourceAccessGrantRequest,
    ResourceAccessGrantResponse,
    ResourceAccessResponse,
)
from app.modules.pod.infrastructure.models.pod_models import Pod, PodMember

router = APIRouter(
    prefix="/pods/{pod_id}/resources/{resource_type}/{resource_name}/access",
    tags=["Pod Resource Access"],
)


@dataclass(frozen=True, slots=True)
class _GrantInput:
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str]


@router.get(
    "",
    response_model=ResourceAccessResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access.get",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def get_resource_access(
    pod_id: UUID,
    resource_type: ResourceType,
    resource_name: str,
    uow: UoWDep,
) -> ResourceAccessResponse:
    resource_id = await _resolve_resource_id(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
    )
    grouped = await list_resource_grants(
        uow.session,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    grants = await _resource_grant_responses(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
        grouped=grouped,
    )
    return ResourceAccessResponse(
        resource_type=resource_type,
        resource_name=resource_name,
        grants=grants,
    )


@router.put(
    "/grantees/{grantee_type}/{grantee_id}",
    response_model=ResourceAccessResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access.grant.replace",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def replace_resource_access_grant(
    pod_id: UUID,
    resource_type: ResourceType,
    resource_name: str,
    grantee_type: str,
    grantee_id: UUID,
    data: ResourceAccessGrantRequest,
    ctx: PodContextDep,
    uow: UoWDep,
) -> ResourceAccessResponse:
    normalized_grantee_type = _normalize_grantee_type(grantee_type)
    resource_id = await _resolve_resource_id(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
    )
    validate_pod_resource_grant_permissions(
        [
            _GrantInput(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=data.permission_ids,
            )
        ]
    )
    await _require_grantee(
        uow,
        pod_id=pod_id,
        grantee_type=normalized_grantee_type,
        grantee_id=grantee_id,
    )
    await replace_resource_grantee_grant(
        uow.session,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
        grantee_type=normalized_grantee_type,
        grantee_id=grantee_id,
        permission_ids=data.permission_ids,
        created_by_user_id=ctx.user_id,
    )
    grouped = await list_resource_grants(
        uow.session,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    grants = await _resource_grant_responses(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
        grouped=grouped,
    )
    return ResourceAccessResponse(
        resource_type=resource_type,
        resource_name=resource_name,
        grants=grants,
    )


@router.delete(
    "/grantees/{grantee_type}/{grantee_id}",
    response_model=ResourceAccessResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access.grant.delete",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def delete_resource_access_grant(
    pod_id: UUID,
    resource_type: ResourceType,
    resource_name: str,
    grantee_type: str,
    grantee_id: UUID,
    uow: UoWDep,
) -> ResourceAccessResponse:
    normalized_grantee_type = _normalize_grantee_type(grantee_type)
    resource_id = await _resolve_resource_id(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
    )
    await _require_grantee(
        uow,
        pod_id=pod_id,
        grantee_type=normalized_grantee_type,
        grantee_id=grantee_id,
    )
    await delete_resource_grantee_grant(
        uow.session,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
        grantee_type=normalized_grantee_type,
        grantee_id=grantee_id,
    )
    grouped = await list_resource_grants(
        uow.session,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    grants = await _resource_grant_responses(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
        grouped=grouped,
    )
    return ResourceAccessResponse(
        resource_type=resource_type,
        resource_name=resource_name,
        grants=grants,
    )


async def _resolve_resource_id(
    uow: UoWDep,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_name: str,
) -> UUID:
    if resource_type not in RESOURCE_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Resource type '{resource_type.value}' does not support sharing",
        )
    resource_id = await resolve_resource_id_by_name(
        uow.session,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_name=resource_name,
    )
    if resource_id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{resource_type.value} '{resource_name}' not found in this pod"
            ),
        )
    return resource_id


def _normalize_grantee_type(grantee_type: str) -> str:
    normalized = grantee_type.strip().upper()
    if normalized not in {"ROLE", "POD_MEMBER"}:
        raise HTTPException(
            status_code=400,
            detail="grantee_type must be ROLE or POD_MEMBER",
        )
    return normalized


async def _require_grantee(
    uow: UoWDep,
    *,
    pod_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
) -> None:
    if grantee_type == "ROLE":
        pod = await uow.session.get(Pod, pod_id)
        if pod is None:
            raise HTTPException(status_code=404, detail="Pod not found")
        await AuthorizationDataService(uow.session).ensure_pod_system_roles(
            organization_id=pod.organization_id,
            pod_id=pod_id,
        )
        stmt = select(RoleModel.id).where(
            RoleModel.pod_id == pod_id,
            RoleModel.id == grantee_id,
        )
        role_id = (await uow.session.execute(stmt)).scalar_one_or_none()
        if role_id is None:
            raise HTTPException(status_code=404, detail="Role not found")
        return

    stmt = select(PodMember.id).where(
        PodMember.pod_id == pod_id,
        PodMember.id == grantee_id,
    )
    pod_member_id = (await uow.session.execute(stmt)).scalar_one_or_none()
    if pod_member_id is None:
        raise HTTPException(status_code=404, detail="Pod member not found")


async def _resource_grant_responses(
    uow: UoWDep,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_name: str,
    grouped: dict[tuple[str, UUID], list[str]],
) -> list[ResourceAccessGrantResponse]:
    role_names = await _role_names_by_id(uow, pod_id=pod_id, grouped=grouped)
    pod_members = await _pod_members_by_id(uow, pod_id=pod_id, grouped=grouped)
    responses: list[ResourceAccessGrantResponse] = []
    for (grantee_type, grantee_id), permission_ids in grouped.items():
        pod_member = (
            pod_members.get(grantee_id) if grantee_type == "POD_MEMBER" else None
        )
        responses.append(
            ResourceAccessGrantResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission_ids=sorted(set(permission_ids)),
                role_name=role_names.get(grantee_id),
                user_id=pod_member["user_id"] if pod_member else None,
                email=pod_member["email"] if pod_member else None,
                display_name=pod_member["display_name"] if pod_member else None,
            )
        )
    return responses


async def _role_names_by_id(
    uow: UoWDep,
    *,
    pod_id: UUID,
    grouped: dict[tuple[str, UUID], list[str]],
) -> dict[UUID, str]:
    role_ids = [
        grantee_id
        for (grantee_type, grantee_id) in grouped
        if grantee_type == "ROLE"
    ]
    if not role_ids:
        return {}
    stmt = select(RoleModel.id, RoleModel.name).where(
        RoleModel.pod_id == pod_id,
        RoleModel.id.in_(role_ids),
    )
    return {role_id: name for role_id, name in (await uow.session.execute(stmt)).all()}


async def _pod_members_by_id(
    uow: UoWDep,
    *,
    pod_id: UUID,
    grouped: dict[tuple[str, UUID], list[str]],
) -> dict[UUID, dict[str, str | UUID | None]]:
    pod_member_ids = [
        grantee_id
        for (grantee_type, grantee_id) in grouped
        if grantee_type == "POD_MEMBER"
    ]
    if not pod_member_ids:
        return {}
    stmt = (
        select(
            PodMember.id,
            OrganizationMember.user_id,
            User.email,
            User.first_name,
            User.last_name,
        )
        .join(
            OrganizationMember,
            PodMember.organization_member_id == OrganizationMember.id,
        )
        .join(User, OrganizationMember.user_id == User.id)
        .where(
            PodMember.pod_id == pod_id,
            PodMember.id.in_(pod_member_ids),
        )
    )
    result: dict[UUID, dict[str, str | UUID | None]] = {}
    for pod_member_id, user_id, email, first_name, last_name in (
        await uow.session.execute(stmt)
    ).all():
        display_name = " ".join(
            part for part in [first_name, last_name] if part
        ) or None
        result[pod_member_id] = {
            "user_id": user_id,
            "email": email,
            "display_name": display_name,
        }
    return result
