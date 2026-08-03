"""Share a resource with an address that may not have an account yet.

Sharing to a stranger previously meant adding them to the organization first,
which is a far larger door than the one being asked for. These routes hold the
intended permissions against an email; the identity signup event turns them into
ordinary ``USER`` grants once an account exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.grants import validate_pod_resource_grant_permissions
from app.core.authorization.permissions import Permissions
from app.core.authorization.resource_actions import RESOURCE_ACTIONS
from app.core.authorization.resource_names import resolve_resource_names_by_ids
from app.core.authorization.service import AuthorizationDataService
from app.modules.pod.api.schemas.pod_schemas import (
    ResourceAccessInviteCreateRequest,
    ResourceAccessInviteListResponse,
    ResourceAccessInviteResponse,
)
from app.modules.pod.services.resource_access_invite_service import (
    ResourceAccessInviteService,
)

router = APIRouter(
    prefix="/pods/{pod_id}/resource-access-invites",
    tags=["Pod Resource Access Invites"],
)


@dataclass(frozen=True, slots=True)
class _GrantInput:
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str]


async def _resolve_existing_resource(
    uow: UoWDep,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID | None,
    resource_name: str | None,
) -> tuple[UUID, str]:
    if resource_type not in RESOURCE_ACTIONS:
        raise HTTPException(status_code=404, detail="Resource not found")
    resource = await AuthorizationDataService(uow.session).resolve_resource_ref(
        resource_type=resource_type,
        pod_id=pod_id,
        resource_id=resource_id,
        resource_name=resource_name,
    )
    if resource is None or resource.resource_id is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    # resolve_resource_ref trusts an id it was handed, so confirm the row exists.
    names = await resolve_resource_names_by_ids(
        uow.session,
        pod_id=pod_id,
        refs=[(resource_type, resource.resource_id)],
    )
    canonical_name = names.get((resource_type, resource.resource_id))
    if canonical_name is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource.resource_id, canonical_name


@router.post(
    "",
    response_model=ResourceAccessInviteResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.resource_access_invite.create",
    summary="Invite an Email to a Resource",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def create_resource_access_invite(
    pod_id: UUID,
    data: ResourceAccessInviteCreateRequest,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> ResourceAccessInviteResponse:
    resource_id, canonical_name = await _resolve_existing_resource(
        uow,
        pod_id=pod_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        resource_name=data.resource_name,
    )
    # An invite becomes a grant verbatim, so it has to be validated as strictly
    # as one — otherwise it is a way to write permissions the grant API rejects.
    validate_pod_resource_grant_permissions(
        [
            _GrantInput(
                resource_type=data.resource_type,
                resource_name=canonical_name,
                permission_ids=list(data.permission_ids),
            )
        ]
    )

    invite = await ResourceAccessInviteService(uow.session).invite(
        pod_id=pod_id,
        resource_type=data.resource_type,
        resource_id=resource_id,
        resource_name=canonical_name,
        email=str(data.email),
        permission_ids=data.permission_ids,
        invited_by_user_id=user.id,
    )
    return ResourceAccessInviteResponse.model_validate(invite)


@router.get(
    "",
    response_model=ResourceAccessInviteListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource_access_invite.list",
    summary="List Pending Invites for a Resource",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def list_resource_access_invites(
    pod_id: UUID,
    resource_type: ResourceType,
    uow: UoWDep,
    ctx: PodContextDep,
    resource_id: UUID | None = None,
    resource_name: str | None = None,
) -> ResourceAccessInviteListResponse:
    resolved_id, _ = await _resolve_existing_resource(
        uow,
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
    )
    invites = await ResourceAccessInviteService(uow.session).list_for_resource(
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resolved_id,
    )
    return ResourceAccessInviteListResponse(
        items=[ResourceAccessInviteResponse.model_validate(item) for item in invites]
    )


@router.delete(
    "/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.resource_access_invite.revoke",
    summary="Revoke a Pending Invite",
    dependencies=[require_action(Permissions.POD_ROLE_MANAGE)],
)
async def revoke_resource_access_invite(
    pod_id: UUID,
    invite_id: UUID,
    uow: UoWDep,
    ctx: PodContextDep,
) -> None:
    await ResourceAccessInviteService(uow.session).revoke(
        invite_id=invite_id, pod_id=pod_id
    )
