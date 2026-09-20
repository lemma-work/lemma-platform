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
    """Who answers, and in which pod -- the destination, not the transport.

    `pod_id` is here rather than read off the surface because the two are not
    always the same thing. A personal DM arrives through a company's
    installation and is answered by the person's own pod, and a surface that
    had to pretend otherwise is how pod data ended up being read from the
    wrong place.
    """

    pod_id: UUID
    agent_id: UUID | None
    agent_name: str | None
    agent_display_name: str
    conversation_kind: str
    route_key: str


@dataclass(frozen=True)
class SurfaceEgressTarget:
    """Resolved destination for an outbound surface message.

    `surface` is the installation the reply goes out through; `pod_id` is the
    pod the conversation belongs to. For a personal DM those differ, so anything
    reading pod data -- a file, a table, a deep link -- must use `pod_id` and
    not `surface.pod_id`.
    """

    link: AgentSurfaceConversationLink
    surface: AgentSurfaceEntity
    pod_id: UUID
    adapter: SurfacePlatformAdapterPort
    event: ParsedInboundSurfaceEvent
    credentials: dict[str, Any]
