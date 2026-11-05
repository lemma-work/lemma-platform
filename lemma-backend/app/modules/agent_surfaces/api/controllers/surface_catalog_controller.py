"""Platform-level surface reads: what a pod can connect, and how to set it up.

Neither needs a surface to exist yet, so neither belongs on the surface
resource router; its own module because ``surface_controller`` is at the size
the architecture ratchet allows.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.connectors.contracts.surfaces import surface_connector
from app.modules.agent_surfaces.api.dependencies import get_surface_service
from app.modules.agent_surfaces.domain.surface_catalog import AvailableSurfacesResponse
from app.modules.agent_surfaces.domain.setup_guides import SurfacePlatformSetupGuide
from app.modules.agent_surfaces.services.available_surfaces_builder import (
    build_available_surfaces,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)

# A surface's platform-level setup checklist (env/OAuth prerequisites) needs no
# surface to exist yet, so it lives outside the surface-resource router.
setup_guide_router = APIRouter(
    prefix="/pods/{pod_id}/surface-setup", tags=["Agent Surfaces"]
)

# The connectable-surface catalog (platform -> connector + supported credential
# modes + connect schema) — also platform-level, needs no surface to exist. Kept
# on its own path so it never collides with GET /surfaces/{surface_name}.
available_surfaces_router = APIRouter(
    prefix="/pods/{pod_id}/available-surfaces", tags=["Agent Surfaces"]
)


@setup_guide_router.get(
    "/{platform}",
    operation_id="agent.surface.setup_guide",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface_setup_guide(
    pod_id: UUID,
    platform: str,
    user: CurrentUser,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfacePlatformSetupGuide:
    """The static pre-creation checklist for a platform (env/OAuth
    prerequisites) — works before any surface of this platform exists."""
    del user, pod_id
    return service.get_platform_setup_guide(platform)


@available_surfaces_router.get(
    "",
    operation_id="agent.surface.available",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_available_surfaces(
    pod_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AvailableSurfacesResponse:
    """The connectable-surface catalog: every surface platform with its connector,
    supported credential modes, the schema to connect an account, and whether this
    pod's org can still claim the platform's Lemma-managed bot/number. Otherwise
    platform-level — no surface need exist."""
    del user
    return await build_available_surfaces(
        read_connector=partial(surface_connector, uow),
        pod_id=pod_id,
        surface_repository=service.surface_repository,
    )
