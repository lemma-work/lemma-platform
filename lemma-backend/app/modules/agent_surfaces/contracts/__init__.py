"""Public surface bundle DTOs."""

from app.modules.agent_surfaces.domain.surface_representation import (
    SurfaceCreateRequest,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceNotFoundError
from app.modules.agent_surfaces.domain.events import SurfaceEvents

SURFACE_EVENTS_STREAM = SurfaceEvents.STREAM

__all__ = [
    "SURFACE_EVENTS_STREAM",
    "AgentSurfaceEntity",
    "AgentSurfaceNotFoundError",
    "SurfaceCreateRequest",
    "SurfacePlatform",
]
