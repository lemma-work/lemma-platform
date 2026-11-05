"""Telling a member that somebody else changed the group they answer for."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.services.direct_messages import tell_member


async def tell_previous_owner(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    previous_owner: UUID,
    actor_id: UUID,
    group: SurfaceGroup,
    what_changed: str,
) -> None:
    """A note in the own channel of the member whose group somebody else
    changed. News, not a question: written on the request's own unit of work,
    and pushed nowhere."""
    people = SqlAlchemySurfaceRoutingResolutionAdapter(uow)
    member_id = await people.get_pod_member_id(previous_owner, pod_id)
    if member_id is None:
        return
    actor = await people.get_user_display_name(actor_id) or "An admin of the space"
    place = group.title or "one of the space's groups"
    await tell_member(
        uow,
        pod_id=pod_id,
        user_id=previous_owner,
        text=f"{actor} {what_changed} in {place}.",
    )
