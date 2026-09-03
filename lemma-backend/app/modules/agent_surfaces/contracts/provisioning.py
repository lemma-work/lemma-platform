"""What another module does to a pod's surfaces when it builds one.

Seven operations, not `AgentSurfaceService`. Two had no publishable name
before this file:

`surface_response` was `_surface_response`, a private helper in the surface
HTTP controller that the pod-bundle exporter reached across two module
boundaries to call. It is how a surface is serialized -- the same object the GET
endpoint returns -- so it is a thing this module means to publish, not a
controller's own business.

`resolve_surface_config` and `merge_surface_config` took an `agent_service`
argument that neither of them used, and every caller had to build one to pass
it. Surface config resolution is about apps and channels; nothing in it asks
about an agent.

A submodule rather than `contracts/__init__`: this reaches the service and API
schema layers, and `contracts/__init__` is imported by anything that wants any
contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.delegation import is_pod_default_agent
from app.modules.agent_surfaces.api.dependencies import get_surface_service
from app.modules.agent_surfaces.api.schemas import (
    AgentSurfaceResponse,
    SurfaceBehaviorConfigInput,
    SurfaceConfigResponse,
    SurfaceConnection,
    SurfaceReach,
)
from app.modules.agent_surfaces.api.surface_config_resolver import (
    merge_surface_config,
    resolve_surface_config,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceNotFoundError
from app.modules.agent_surfaces.platforms.common import computed_webhook_url


def surface_response(
    surface: AgentSurfaceEntity,
    *,
    agent_name: str | None = None,
    reach: SurfaceReach | None = None,
    connection: SurfaceConnection | None = None,
) -> AgentSurfaceResponse:
    """One surface as this module serializes it, for its API and for export."""
    return AgentSurfaceResponse(
        id=surface.id,
        pod_id=surface.pod_id,
        name=surface.name,
        agent_id=surface.agent_id,
        agent_name=agent_name,
        uses_default_agent=is_pod_default_agent(
            surface.agent_id, pod_id=surface.pod_id
        ),
        platform=surface.surface_type,
        credential_mode=surface.credential_mode,
        account_id=surface.account_id,
        connection=connection,
        surface_identity_id=surface.surface_identity_id,
        surface_identity_username=surface.surface_identity_username,
        surface_identity_email=surface.surface_identity_email,
        webhook_url=computed_webhook_url(surface),
        reach=reach,
        config=SurfaceConfigResponse.from_domain(surface.config),
        status=surface.status,
    )


async def list_surfaces(uow, *, pod_id: UUID) -> list[AgentSurfaceEntity]:
    """Every surface configured on the pod."""
    surfaces, _ = await get_surface_service(uow).list_surfaces_by_pod(pod_id, limit=100)
    return list(surfaces)


async def get_surface_by_name(
    uow, *, pod_id: UUID, name: str
) -> AgentSurfaceEntity | None:
    """The named surface, or ``None`` when the pod does not have one.

    Name, not platform: a pod may run several surfaces on one platform, and
    keying this by platform made a second Slack surface look like the first.
    """
    try:
        return await get_surface_service(uow).get_surface_by_name_in_pod(
            pod_id=pod_id, name=name
        )
    except AgentSurfaceNotFoundError:
        return None


async def create_surface(
    uow,
    *,
    pod_id: UUID,
    agent: object | None,
    platform: SurfacePlatform,
    name: str,
    config: SurfaceConfig,
    credential_mode: SurfaceCredentialMode | None,
    account_id: UUID | None,
    ctx: Context,
) -> AgentSurfaceEntity:
    """Create a surface, minting the address a platform needs one for."""
    return await get_surface_service(uow).create_surface_minting_address(
        pod_id=pod_id,
        agent=agent,
        platform=platform,
        name=name,
        config=config,
        credential_mode=credential_mode,
        account_id=account_id,
        ctx=ctx,
    )


async def update_surface(
    uow,
    *,
    surface_id: UUID,
    agent_id: UUID | None = None,
    update_agent_id: bool = False,
    config: SurfaceConfig | None = None,
    credential_mode: SurfaceCredentialMode | None = None,
    account_id: UUID | None = None,
    is_active: bool | None = None,
    ctx: Context,
) -> AgentSurfaceEntity:
    """Change a configured surface."""
    return await get_surface_service(uow).update_surface(
        surface_id=surface_id,
        agent_id=agent_id,
        update_agent_id=update_agent_id,
        config=config,
        credential_mode=credential_mode,
        account_id=account_id,
        is_active=is_active,
        ctx=ctx,
    )


async def build_surface_config(
    uow,
    *,
    pod_id: UUID,
    platform: SurfacePlatform,
    config_input: SurfaceBehaviorConfigInput,
    ctx: Context,
) -> SurfaceConfig:
    """The config a new surface starts with, with its app references resolved."""
    return await resolve_surface_config(
        uow=uow, pod_id=pod_id, platform=platform, config_input=config_input, ctx=ctx
    )


async def apply_surface_config(
    uow,
    *,
    pod_id: UUID,
    platform: SurfacePlatform,
    existing: SurfaceConfig,
    config_input: SurfaceBehaviorConfigInput,
    ctx: Context,
) -> SurfaceConfig:
    """An existing config with only the fields the caller set overwritten."""
    return await merge_surface_config(
        uow=uow,
        pod_id=pod_id,
        existing=existing,
        platform=platform,
        config_input=config_input,
        ctx=ctx,
    )


__all__ = [
    "apply_surface_config",
    "build_surface_config",
    "create_surface",
    "get_surface_by_name",
    "list_surfaces",
    "surface_response",
    "update_surface",
]
