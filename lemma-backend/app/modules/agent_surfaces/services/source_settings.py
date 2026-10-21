"""A group source's settings: who answers for its outsiders, and whether they are.

A WhatsApp or Telegram group, a Slack or Teams channel, a group DM: each is one
source of a GROUP channel (``app.modules.channels``), and what was said there is
that channel's timeline. What is left here is what only the bot's side knows --
which bot is in it, whether a stranger who asks there is answered, and which
member answers for them.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)


class SourceState(BaseModel):
    group: SurfaceGroup
    surface_name: str
    agent_id: UUID | None = None
    owner_name: str | None = None
    #: Whether the owner is still in the pod. One who has left answers for
    #: nobody, and the group is anybody's to take on.
    owner_in_pod: bool = False
    #: The bot's own switch for people outside the pod, over all its groups.
    bot_answers_outsiders: bool = True

    @property
    def answers_outsiders_now(self) -> bool:
        """Whether a stranger who asks in this group today is answered."""
        return (
            self.group.welcomes_outsiders
            and self.owner_in_pod
            and self.bot_answers_outsiders
        )


class SourceSettings:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.groups = SurfaceGroupRepository(uow.session)
        self.people = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

    async def of(self, *, pod_id: UUID, source_id: UUID) -> SourceState | None:
        """A group source of this pod, with what its settings depend on."""
        group = await self.groups.get_by_id(source_id)
        if group is None or group.pod_id != pod_id:
            return None
        surface = await self.surface(group.surface_id)
        if surface is None or surface.pod_id != pod_id:
            return None
        owner = group.owner_user_id
        in_pod = (
            await self.people.pod_members_among(pod_id, {owner}) if owner else set()
        )
        names = await self.people.display_names({owner}) if owner else {}
        return SourceState(
            group=group,
            surface_name=surface.name,
            agent_id=surface.agent_id,
            owner_name=names.get(owner) if owner else None,
            owner_in_pod=owner in in_pod,
            bot_answers_outsiders=surface.config.groups.answers_outsiders,
        )

    async def of_surface(
        self, *, pod_id: UUID, surface: AgentSurfaceEntity
    ) -> list[SourceState]:
        """Every group one of the pod's bots is in, most recently changed first."""
        groups = [
            group
            for group in await self.groups.list_for_pod(pod_id)
            if group.surface_id == surface.id
        ]
        owners = {group.owner_user_id for group in groups if group.owner_user_id}
        in_pod = (
            await self.people.pod_members_among(pod_id, owners) if owners else set()
        )
        names = await self.people.display_names(owners) if owners else {}
        return [
            SourceState(
                group=group,
                surface_name=surface.name,
                agent_id=surface.agent_id,
                owner_name=names.get(group.owner_user_id)
                if group.owner_user_id
                else None,
                owner_in_pod=group.owner_user_id in in_pod,
                bot_answers_outsiders=surface.config.groups.answers_outsiders,
            )
            for group in groups
        ]

    async def surface(self, surface_id: UUID | None) -> AgentSurfaceEntity | None:
        """The bot a group belongs to."""
        if surface_id is None:
            return None
        return await SurfaceRepository(self.uow).get(surface_id)

    async def change(
        self,
        source_id: UUID,
        *,
        new_owner: UUID | None,
        answers_outsiders: bool | None,
    ) -> None:
        """Take a group over, switch its outsiders, or both; each only if given."""
        if new_owner is not None:
            await self.groups.set_owner(source_id, new_owner)
        if answers_outsiders is not None:
            await self.groups.set_answers_outsiders(
                source_id, answers_outsiders=answers_outsiders
            )
