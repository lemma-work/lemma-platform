"""Public surface bundle DTOs."""

from app.modules.agent_surfaces.api.schemas import SurfaceCreateRequest
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceNotFoundError
from app.modules.agent_surfaces.domain.events import (
    NotificationClosedEvent,
    NotificationSettledEvent,
    SurfaceEvents,
)
from app.modules.agent_surfaces.domain.notification import (
    CHOICE_ACTION,
    NotificationOriginKind,
)

SURFACE_EVENTS_STREAM = SurfaceEvents.STREAM

__all__ = [
    "CHOICE_ACTION",
    "SURFACE_EVENTS_STREAM",
    "AgentSurfaceEntity",
    "AgentSurfaceNotFoundError",
    "NotificationClosedEvent",
    "NotificationOriginKind",
    "NotificationSettledEvent",
    "SurfaceCreateRequest",
    "SurfacePlatform",
]
