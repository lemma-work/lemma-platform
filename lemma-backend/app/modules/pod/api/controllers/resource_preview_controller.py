"""What a shared link can tell you about the thing it points at.

The workspace answers "can I open this?" only by trying to render the whole pod
around it, which is no answer at all for someone who was sent one link and is not
a member. This route answers it directly: resolve the resource, run the ordinary
authorization for its read action, and either describe it or decline.

Deliberately *not* gated on pod membership — that is the entire point. The
authorization decision comes from the resource's own visibility, exactly as it
does when the resource is opened for real, so this can never report access that
the read path would then refuse.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.core.api.dependencies import UoWDep
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.resource_actions import RESOURCE_ACTIONS
from app.core.authorization.resource_names import resolve_resource_names_by_ids
from app.core.authorization.service import AuthorizationDataService, Authorizer
from app.core.authorization.sql_actions import read_action_for_resource
from app.modules.pod.api.schemas.pod_schemas import ResourcePreviewResponse

# The resource name rides in the query, not the path. A document's name *is* its
# path ("/library/notes.md"), and a slash-bearing value cannot survive a single
# path segment: the server decodes %2F before routing, so the route simply never
# matches and the caller gets a bare 404 that looks like "no such document".
router = APIRouter(
    prefix="/pods/{pod_id}/resources/{resource_type}",
    tags=["Pod Resource Preview"],
)

# One message for "no such resource" and for "not yours to see", so the route
# cannot be used to discover which names exist in a pod you have no access to.
_OPAQUE_DENIAL = "No resource with that name is available to you."


@router.get(
    "/preview",
    response_model=ResourcePreviewResponse,
    status_code=status.HTTP_200_OK,
    operation_id="pod.resource.preview",
    summary="Preview a Shared Resource",
)
async def preview_resource(
    pod_id: UUID,
    resource_type: ResourceType,
    uow: UoWDep,
    ctx: PodContextDep,
    resource_name: str | None = Query(default=None, alias="name"),
    resource_id: UUID | None = Query(default=None, alias="id"),
) -> ResourcePreviewResponse:
    """Describe a shared resource, addressed by id or by name.

    Both, because the two live in different worlds: agents, apps and tables are
    linked by name, while a document's "name" is its stored path — which a
    recipient does not have, since the link they were sent carries an id
    precisely so it does not depend on a path.
    """
    if resource_type not in RESOURCE_ACTIONS:
        raise HTTPException(status_code=404, detail=_OPAQUE_DENIAL)
    if resource_id is None and resource_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either 'id' or 'name'.",
        )

    resource = await AuthorizationDataService(uow.session).resolve_resource_ref(
        resource_type=resource_type,
        pod_id=pod_id,
        resource_id=resource_id,
        resource_name=resource_name,
    )
    if resource is None or resource.resource_id is None:
        raise HTTPException(status_code=404, detail=_OPAQUE_DENIAL)

    # ``resolve_resource_ref`` hands back an id it was *given* without checking
    # that anything has it, so an id-addressed lookup must confirm existence
    # here. Resolving the name does both jobs: it proves the row is real and
    # supplies the display name an id-addressed caller does not have.
    canonical_name = await _resolve_name(
        uow, pod_id=pod_id, resource_type=resource_type, resource_id=resource.resource_id
    )
    if canonical_name is None:
        raise HTTPException(status_code=404, detail=_OPAQUE_DENIAL)
    resource_name = canonical_name

    # resolve_resource_ref returns the identity triple only; the reported fields
    # (visibility, owner) come from hydration.
    resource = await Authorizer(uow.session).hydrate_resource_ref(resource)

    read_action = read_action_for_resource(resource_type)
    if not await ctx.can(read_action, resource):
        # 404 rather than 403: a 403 would confirm the resource exists, which is
        # itself worth knowing to someone probing pod contents by name.
        raise HTTPException(status_code=404, detail=_OPAQUE_DENIAL)

    return ResourcePreviewResponse(
        resource_type=resource_type,
        resource_name=resource_name,
        resource_id=resource.resource_id,
        pod_id=pod_id,
        visibility=(
            resource.visibility.value if resource.visibility is not None else None
        ),
        owner_user_id=resource.owner_user_id,
        allowed_actions=[
            action
            for action in RESOURCE_ACTIONS.get(resource_type, ())
            if await ctx.can(action, resource)
        ],
    )


async def _resolve_name(
    uow: UoWDep,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    resource_id: UUID,
) -> str | None:
    """The resource's public name, or None when nothing has that id."""
    names = await resolve_resource_names_by_ids(
        uow.session,
        pod_id=pod_id,
        refs=[(resource_type, resource_id)],
    )
    return names.get((resource_type, resource_id))
