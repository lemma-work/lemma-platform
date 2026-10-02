"""Services and permission checks for the decisions routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.dependencies import (
    pod_from_path,
    require_action,
    require_resource_action,
    require_resource_admin_or_creator,
)
from app.core.authorization.permissions import Permissions
from app.modules.decisions.services.deciders_service import DecidersService
from app.modules.decisions.services.decisions_service import Asker, DecisionsService
from app.modules.decisions.services.system_deciders import SYSTEM_PREFIX
from app.modules.decisions.services.wiring import (
    build_deciders_service,
    build_decisions_service,
)


def get_decisions_service() -> DecisionsService:
    return build_decisions_service()


def get_deciders_service() -> DecidersService:
    return build_deciders_service()


DecisionsServiceDep = Annotated[DecisionsService, Depends(get_decisions_service)]
DecidersServiceDep = Annotated[DecidersService, Depends(get_deciders_service)]

DeciderReadDep = require_action(Permissions.DECIDER_READ, pod_from_path)
DeciderCreateDep = require_action(Permissions.DECIDER_CREATE, pod_from_path)
DeciderResourceReadDep = require_resource_action(
    Permissions.DECIDER_READ,
    resource_type=ResourceType.DECIDER,
    name_param="decider_name",
)
DeciderResourceUpdateDep = require_resource_action(
    Permissions.DECIDER_UPDATE,
    resource_type=ResourceType.DECIDER,
    name_param="decider_name",
)
DeciderResourceDeleteDep = require_resource_admin_or_creator(
    Permissions.DECIDER_DELETE,
    resource_type=ResourceType.DECIDER,
    name_param="decider_name",
)


def asker_from(ctx: Context, visibility: str = "PERSONAL") -> Asker:
    """The asker for a request: the person, in the pod the route names."""
    return Asker(
        user_id=ctx.user_id,
        pod_id=ctx.pod_id,
        organization_id=ctx.organization_id,
        visibility=visibility,
    )


async def authorize_asking(
    *,
    ctx: Context,
    deciders: DecidersService,
    pod_id: UUID,
    decider: str | None,
) -> None:
    """Ask a pod decider under its own grant, and inline or system questions under the pod's.

    Checked on what the request names rather than up front on the pod: a
    workload granted one decider (`decider:<name>:execute`) holds no pod-wide
    permission, and must be able to ask that decider and nothing else.
    """
    if decider is None or decider.startswith(SYSTEM_PREFIX):
        resource = ResourceRef.pod(pod_id)
    else:
        found = await deciders.get(pod_id=pod_id, name=decider)
        resource = ResourceRef(
            resource_type=ResourceType.DECIDER, resource_id=found.id, pod_id=pod_id
        )
    await ctx.require(Permissions.DECIDER_EXECUTE, resource)
