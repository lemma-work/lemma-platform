"""Letting a surface's teammate answer in one more channel.

A surface has one agent, so a channel is a place rather than a choice: adding
it twice is the same as adding it once. The channel becomes one of the pod's
groups, and whoever connected it answers for the people outside the pod in it.

Shared by the two ways a channel gets one: the teammate's own bot being invited
by someone who may set it up, and Lemma's manager bringing the teammate in.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.credentials import CredentialsLike
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_config import SurfaceChannelRoute
from app.modules.agent_surfaces.services.group_registry import adopt_connected_channel
from app.modules.pod.contracts.members import pod_name


async def allow_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces: SurfaceInstallationRepositoryPort,
    surface: AgentSurfaceEntity,
    channel_id: str,
    owner_user_id: UUID | None = None,
) -> None:
    if not channel_id:
        return
    routes = [
        route
        for route in surface.config.channels
        if str(route.channel_id or "") != channel_id
    ]
    routes.append(SurfaceChannelRoute(channel_id=channel_id))
    surface.config.channels = routes
    await surfaces.update(surface)
    await adopt_connected_channel(
        uow, surface=surface, channel_id=channel_id, owner_user_id=owner_user_id
    )
    await uow.commit()


async def welcome_in_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces: SurfaceInstallationRepositoryPort,
    surface: AgentSurfaceEntity,
    credentials: CredentialsLike,
    channel_id: str,
    owner_user_id: UUID | None,
) -> None:
    """Take the channel, and say one line so the room knows who just arrived."""
    from app.modules.agent_surfaces.platforms.slack.workspace_calls import post_message
    from app.modules.agent_surfaces.services.slack_manager_context import teammate_name

    await allow_channel(
        uow,
        surfaces=surfaces,
        surface=surface,
        channel_id=channel_id,
        owner_user_id=owner_user_id,
    )
    name = teammate_name(await pod_name(uow.session, surface.pod_id) or "")
    hello = (
        f"Hi, I'm {name}. Mention me to ask me something."
        if name
        else ("Hi. Mention me to ask me something.")
    )
    async with connection_released(uow.session):
        await post_message(credentials, channel=channel_id, text=hello)
