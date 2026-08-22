"""Resolved route and destination, shared by the ingress mixins.

These two were defined in :mod:`ingress_service`, which is where they were used
from until the service was split. They cannot stay there: every mixin that needs
one is imported *by* that module, so reaching back for the type would be a
cycle. A leaf module both sides can import is the way out.

`SurfaceEgressTarget` loses its leading underscore in the move -- it is named
across modules now, and a private name that half the package imports is only
private by spelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.ports import SurfacePlatformAdapterPort


@dataclass(frozen=True)
class ResolvedSurfaceRoute:
    agent_id: UUID | None
    agent_name: str | None
    agent_display_name: str
    conversation_kind: str
    route_key: str


@dataclass(frozen=True)
class SurfaceEgressTarget:
    """Resolved destination for an outbound surface message."""

    link: AgentSurfaceConversationLink
    surface: AgentSurfaceEntity
    adapter: SurfacePlatformAdapterPort
    event: ParsedInboundSurfaceEvent
    credentials: dict[str, Any]
