"""What the caller may do in one organization, as permission ids.

The org-level twin of ``/pods/{pod_id}/permissions/me``. A page asks for a
permission (``org.installation.manage``), never for a role, so the day a role's
holdings change no page has to. Before this route the web app read the caller's
role off the navigation response and turned it into permissions with its own
copy of ``SYSTEM_ROLE_PERMISSIONS``: a second answer to the question, and two
answers drift.

Asked of the authorizer, one permission at a time, the way the pod route asks:
whatever decides a real org-level request decides this list, so the list cannot
promise what a request would then refuse. Somebody outside the organization
holds nothing there and gets an empty list, which says no more than a 404 would.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import OrgContextDep
from app.core.authorization.permissions import PERMISSION_DEFINITIONS, PermissionScope
from app.modules.identity.api.schemas.organization_schemas import (
    OrganizationEffectivePermissionsResponse,
)

router = APIRouter(
    prefix="/organizations/{organization_id}/permissions", tags=["Organizations"]
)

#: Every organization-scoped permission, in a stable order.
_ORG_PERMISSIONS = sorted(
    definition.id
    for definition in PERMISSION_DEFINITIONS
    if definition.scope is PermissionScope.ORG
)


@router.get(
    "/me",
    response_model=OrganizationEffectivePermissionsResponse,
    operation_id="org.permissions.me",
    summary="My Permissions In An Organization",
    description=(
        "The organization-level permission ids the caller holds here, such as "
        "`org.installation.manage`. Pod permissions are "
        "`/pods/{pod_id}/permissions/me`."
    ),
)
async def get_my_organization_permissions(
    organization_id: UUID, ctx: OrgContextDep
) -> OrganizationEffectivePermissionsResponse:
    resource = ResourceRef.organization(organization_id)
    return OrganizationEffectivePermissionsResponse(
        organization_id=organization_id,
        actions=[
            permission_id
            for permission_id in _ORG_PERMISSIONS
            if await ctx.can(permission_id, resource)
        ],
    )
