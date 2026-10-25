"""Which surfaces run through a set of pod connectors.

For ``connectors``' installation impact preview: a surface bound to a pod
connector answers as that connector's account, so removing the installation
behind it leaves the surface with nothing to answer as. Surfaces on Lemma's own
system credentials name no connector and are not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.infrastructure.models import AgentSurface


@dataclass(frozen=True, slots=True)
class SurfaceOnConnector:
    surface_id: UUID
    pod_id: UUID
    pod_connector_slug: str
    #: slack, telegram, whatsapp, email, teams.
    platform: str
    name: str
    status: str


async def surfaces_on_pod_connectors(
    session: AsyncSession,
    connectors: Sequence[tuple[UUID, str]],
    *,
    limit: int,
) -> list[SurfaceOnConnector]:
    """The surfaces bound to any of these ``(pod_id, slug)`` connectors, in
    any state, at most ``limit`` of them."""
    if not connectors:
        return []
    rows = await session.execute(
        select(
            AgentSurface.id,
            AgentSurface.pod_id,
            AgentSurface.pod_connector_slug,
            AgentSurface.surface_type,
            AgentSurface.name,
            AgentSurface.status,
        )
        .where(
            tuple_(AgentSurface.pod_id, AgentSurface.pod_connector_slug).in_(
                list(connectors)
            )
        )
        .order_by(AgentSurface.created_at, AgentSurface.id)
        .limit(limit)
    )
    return [
        SurfaceOnConnector(
            surface_id=surface_id,
            pod_id=pod_id,
            pod_connector_slug=slug,
            platform=str(platform),
            name=name,
            status=str(status),
        )
        for surface_id, pod_id, slug, platform, name, status in rows
    ]


__all__ = ["SurfaceOnConnector", "surfaces_on_pod_connectors"]
