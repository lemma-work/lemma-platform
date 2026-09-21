"""Where a pod's agents can be reached, as a listing.

The runtime brief tells an agent which platform the *current* run arrived on and
said nothing about any other, so an agent answering in the web UI could not tell
somebody it was also on Slack, and one answering in Slack did not know it had an
email address.

Only ACTIVE surfaces. A surface still waiting on admin consent or setup is not
somewhere anyone can reach you, and listing it invites the agent to offer it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.agent_surfaces.domain.entities import AgentSurfaceStatus
from app.modules.agent_surfaces.infrastructure.models import AgentSurface


@dataclass(frozen=True, slots=True)
class PodSurfaceSummary:
    #: slack, telegram, whatsapp, email, teams.
    platform: str
    name: str
    #: Which agent answers here. A pod can carry surfaces bound to other agents.
    agent_id: UUID
    #: The address as a person would type it -- a username, a number, a mailbox.
    handle: str | None


async def list_surface_summaries(
    *, session, pod_id: UUID, limit: int
) -> list[PodSurfaceSummary]:
    rows = (
        await session.execute(
            select(
                AgentSurface.surface_type,
                AgentSurface.name,
                AgentSurface.agent_id,
                AgentSurface.surface_identity_username,
                AgentSurface.surface_identity_email,
            )
            .where(
                AgentSurface.pod_id == pod_id,
                AgentSurface.status == AgentSurfaceStatus.ACTIVE.value,
            )
            .order_by(AgentSurface.created_at.asc())
            .limit(limit)
        )
    ).all()
    return [
        PodSurfaceSummary(
            platform=str(surface_type),
            name=name,
            agent_id=agent_id,
            handle=username or email,
        )
        for surface_type, name, agent_id, username, email in rows
    ]
