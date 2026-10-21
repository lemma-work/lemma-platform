"""Letting a surface's teammate answer in one more channel.

A surface has one agent, so a channel is a place rather than a choice: adding
it twice is the same as adding it once. The channel becomes one of the pod's
groups, and whoever connected it answers for the people outside the pod in it.

Shared by the two ways a channel gets one: the configuration prompt the
teammate's own bot shows when invited, and Lemma's manager bringing the
teammate in (``services/slack_manager``), which writes the channel first and
takes it back if Slack then refuses the invite.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_config import SurfaceChannelRoute
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.group_registry import adopt_connected_channel


@dataclass(frozen=True, slots=True)
class AllowedChannel:
    """What allowing a channel changed, so it can be taken back."""

    channel_id: str
    #: The route was not there before.
    route_added: bool
    group_id: UUID | None
    #: The group was made now, not found.
    group_created: bool


async def allow_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces: SurfaceInstallationRepositoryPort,
    surface: AgentSurfaceEntity,
    channel_id: str,
    owner_user_id: UUID | None = None,
    is_private: bool = True,
) -> AllowedChannel | None:
    """Route the channel to the surface and make it one of the pod's groups.
    Commits. ``is_private`` as ``adopt_connected_channel`` takes it."""
    if not channel_id:
        return None
    route_added = not any(
        str(route.channel_id or "") == channel_id for route in surface.config.channels
    )
    routes = [
        route
        for route in surface.config.channels
        if str(route.channel_id or "") != channel_id
    ]
    routes.append(SurfaceChannelRoute(channel_id=channel_id))
    surface.config.channels = routes
    await surfaces.update(surface)
    adopted = await adopt_connected_channel(
        uow,
        surface=surface,
        channel_id=channel_id,
        owner_user_id=owner_user_id,
        is_private=is_private,
    )
    await uow.commit()
    return AllowedChannel(
        channel_id=channel_id,
        route_added=route_added,
        group_id=adopted[0] if adopted else None,
        group_created=bool(adopted and adopted[1]),
    )


async def take_back_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces: SurfaceInstallationRepositoryPort,
    surface_id: UUID,
    allowed: AllowedChannel,
) -> None:
    """Undo what ``allow_channel`` added, for a bot that never got in. Commits.

    Only what it added: a route or a group that was already there stays. A
    group made for nothing is archived, as one the bot was taken out of is.
    """
    surface = await surfaces.get(surface_id)
    if surface is not None and allowed.route_added:
        surface.config.channels = [
            route
            for route in surface.config.channels
            if str(route.channel_id or "") != allowed.channel_id
        ]
        await surfaces.update(surface)
    if allowed.group_created and allowed.group_id is not None:
        await SurfaceGroupRepository(uow.session).bot_removed(allowed.group_id)
    await uow.commit()
