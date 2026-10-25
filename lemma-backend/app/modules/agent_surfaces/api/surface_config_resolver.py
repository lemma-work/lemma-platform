from __future__ import annotations

from uuid import UUID


from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.services.surface_connection_resolver import (
    SurfaceConnectionResolver,
)


async def require_surface_agent_action(
    *,
    ctx,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> None:
    # The pod's own assistant is asked about pod-scoped, the same as everywhere
    # else. Its row's id is the pod's, so both arms would name the same thing --
    # but grants match on (type, id), and an AGENT-typed check would newly hit
    # the resource-owner shortcut for whoever created the pod.
    if agent_id is None or is_pod_default_agent(agent_id, pod_id=pod_id):
        return
    await ctx.require(
        action,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            pod_id=pod_id,
        ),
    )


async def may_perform_surface_agent_action(
    *,
    ctx,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> bool:
    """``require_surface_agent_action``'s question, asked rather than told.

    Same two arms — a pod-scoped check for the pod's own assistant, an
    agent-scoped one otherwise — so the two cannot disagree about what an
    editor is.
    """
    if agent_id is None or is_pod_default_agent(agent_id, pod_id=pod_id):
        return await ctx.can(action)
    return await ctx.can(
        action,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            pod_id=pod_id,
        ),
    )


async def surface_setup_for_reader(
    *,
    service,
    ctx,
    connection_resolver: SurfaceConnectionResolver,
    pod_id: UUID,
    surface_name: str,
    viewer_user_id: UUID,
) -> dict[str, object]:
    """This surface's setup state, with only what this reader may be shown.

    ``SurfaceSetupActionField.secret`` is a rendering hint, not an access
    control, and the WhatsApp verify token in one of those fields is what
    re-points the org's webhook subscription. The endpoint is readable with
    ``AGENT_READ``, so every pod member who can list agents was handed it;
    refusing them the whole checklist would be wrong, so the one value goes to a
    reader who could change the surface anyway.
    """
    surface = await service.get_surface_by_name_in_pod(pod_id=pod_id, name=surface_name)
    return await service.get_surface_setup_by_name(
        pod_id=pod_id,
        name=surface_name,
        reveal_secrets=await may_perform_surface_agent_action(
            ctx=ctx,
            pod_id=pod_id,
            agent_id=surface.agent_id,
            action=Permissions.AGENT_UPDATE,
        ),
        connection=await connection_resolver.for_surface(
            surface, pod_id=pod_id, viewer_user_id=viewer_user_id
        ),
    )
