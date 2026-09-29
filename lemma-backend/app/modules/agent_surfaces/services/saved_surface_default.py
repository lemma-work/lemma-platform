"""The questions routing asks about a person's saved default surface.

Two callers ask them and must agree: ordinary selection (`SurfaceRouter`), which
honours the default and clears one that has gone stale, and the verified personal
route, which steps aside for a default selection would honour. They share the
predicates here so that a message cannot be routed to a default by one path and
to a personal pod by the other. Functions taking their ports, because each needs
two of them and calls back into neither.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)


async def default_still_stands(
    surfaces: SurfaceInstallationRepositoryPort,
    *,
    surface_id: UUID,
    platform: str,
    user_pod_ids: set[UUID],
) -> bool:
    """Is this saved surface one the person could still be sent to?

    The candidate query asked of one id and nothing else -- so liveness means
    here exactly what it means there (ACTIVE, in a pod that has not been deleted),
    and the difference is only that none of the *delivery's* narrowings apply.
    Pod membership is checked separately because ``get_user_pod_ids`` answers for
    deleted pods too.
    """
    live = await surfaces.list_active_for_routing(platform, surface_ids=[surface_id])
    return any(surface.pod_id in user_pod_ids for surface in live)


async def deliverable_default(
    *,
    membership: SurfacePodMembershipPort,
    surfaces: SurfaceInstallationRepositoryPort,
    user_id: UUID,
    parsed: ParsedInboundSurfaceEvent,
    receiver_surface_ids: list[UUID] | None,
    system_credentials_only: bool = False,
) -> AgentSurfaceEntity | None:
    """The saved default, if ordinary selection could route this delivery to it.

    Live, in a pod the person is still in, served by the bot that delivered this
    event, and willing to act on it. Where it says no, selection would not pick
    the default either. A read: clearing a stale default is selection's.

    ``system_credentials_only`` is selection's own narrowing for an event that
    arrived on the shared system bot: a surface running on its own account's
    credentials is not somewhere that event can be answered, so a default that is
    one is not deliverable either.
    """
    platform = parsed.platform.value
    default_id = await membership.get_user_default_surface_id(user_id, platform)
    if default_id is None:
        return None
    if receiver_surface_ids is not None and default_id not in receiver_surface_ids:
        return None
    live = await surfaces.list_active_for_routing(
        platform,
        surface_ids=[default_id],
        system_credentials_only=system_credentials_only,
    )
    surface = next((row for row in live if row.id == default_id), None)
    if surface is None or not surface.allows_inbound_event(parsed):
        return None
    user_pod_ids = set(await membership.get_user_pod_ids(user_id))
    return surface if surface.pod_id in user_pod_ids else None
