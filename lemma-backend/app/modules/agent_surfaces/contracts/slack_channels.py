"""Whether a Slack channel is one of a pod's, for a reader in another module.

A Slack channel is a pod's when somebody connected it: routed on one of the
pod's Slack surfaces, one by one (``workspace_channels``). Its place, which is
what a heard message is fired in (``channel_triggers``), is made the first
time the bot hears it there. A schedule that watches a channel asks this when
it is created, so one on a channel the pod's bot never hears is refused while
its author is still there to be told, rather than stored and never fired.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface

_MOST_SURFACES = 100


async def pod_hears_slack_channel(
    session: AsyncSession, *, pod_id: UUID, team_id: str, channel_id: str
) -> bool:
    """Whether the pod's bot hears ``channel_id`` in workspace ``team_id``.

    True for a channel routed by its id on one of the pod's active Slack
    surfaces in that workspace. A route that names a channel only by name
    cannot be matched to an id until the channel is heard, so a surface with
    one is given the benefit of the doubt.
    """
    rows = (
        await session.execute(
            select(AgentSurface)
            .where(
                AgentSurface.pod_id == pod_id,
                AgentSurface.surface_type == SurfacePlatform.SLACK.value,
                AgentSurface.status == AgentSurfaceStatus.ACTIVE.value,
                AgentSurface.external_workspace_id == team_id,
            )
            # One per agent and platform (`uq_agent_surface_agent_type`), so a
            # pod has as many as it has agents on Slack: a handful.
            .limit(_MOST_SURFACES)
        )
    ).scalars()
    for row in rows:
        surface = row.to_entity_or_none()
        if surface is None:
            continue
        if surface.channel_route_for(channel_id=channel_id) is not None:
            return True
        if any(
            not str(route.channel_id or "").strip() for route in surface.config.channels
        ):
            return True
    return False


__all__ = ["pod_hears_slack_channel"]
