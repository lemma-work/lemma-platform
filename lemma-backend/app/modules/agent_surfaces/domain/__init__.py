"""Domain models for agent surfaces."""

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    ExternalSurfaceUserEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceNotFoundError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceMessageMetadata,
    SurfaceSenderProfile,
)

__all__ = [
    "AgentSurfaceEntity",
    "AgentSurfaceNotFoundError",
    "AgentSurfaceStatus",
    "AgentSurfaceValidationError",
    "ExternalSurfaceUserEntity",
    "ParsedInboundSurfaceEvent",
    "ResolvedSurfaceUser",
    "SurfaceConfig",
    "SurfaceDirectWebhookIngress",
    "SurfaceIngressRequest",
    "SurfaceMessageMetadata",
    "SurfacePlatform",
    "SurfacePlatformWebhookIngress",
    "SurfaceSenderProfile",
]
