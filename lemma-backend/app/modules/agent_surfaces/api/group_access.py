"""What one reader may do with a pod's group sources.

Reading a group takes what reading its bot takes. Changing one -- switching its
people outside the pod on or off, or taking it over -- is narrower: the member
who answers for it; anybody who may configure the bot, when nobody in the pod
answers for it; or an admin of the pod. Anyone else who configures the bot could
otherwise take a colleague's clients off them, or open their group to
strangers, without a word to them.

When an admin does change somebody's group, that member is told
(`services.group_owner_notes`).
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.api.surface_config_resolver import (
    may_perform_surface_agent_action,
)
from app.modules.agent_surfaces.services.source_settings import (
    SourceSettings,
    SourceState,
)


class GroupAccess:
    """One reader's rights over the pod's groups, each asked once per bot."""

    def __init__(
        self, *, ctx, uow: SqlAlchemyUnitOfWork, pod_id: UUID, viewer_id: UUID
    ) -> None:
        self.ctx = ctx
        self.uow = uow
        self.pod_id = pod_id
        self.viewer_id = viewer_id
        self._agents: dict[UUID, UUID | None] = {}
        self._decided: dict[tuple[UUID, str], bool] = {}
        self._admin: bool | None = None

    async def reads(self, surface_id: UUID) -> bool:
        return await self._may(surface_id, Permissions.AGENT_READ)

    async def configures(self, surface_id: UUID) -> bool:
        return await self._may(surface_id, Permissions.AGENT_UPDATE)

    async def is_admin(self) -> bool:
        if self._admin is None:
            self._admin = bool(await self.ctx.can(Permissions.POD_MEMBER_MANAGE))
        return self._admin

    async def manages(self, summary: SourceState) -> bool:
        """Whether the reader may switch this group's outsiders or take it over."""
        if not await self.configures(summary.group.surface_id):
            return False
        owner = summary.group.owner_user_id
        if owner is None or not summary.owner_in_pod or owner == self.viewer_id:
            return True
        return await self.is_admin()

    async def _may(self, surface_id: UUID, action: str) -> bool:
        key = (surface_id, action)
        if key not in self._decided:
            self._decided[key] = await may_perform_surface_agent_action(
                ctx=self.ctx,
                pod_id=self.pod_id,
                agent_id=await self._agent_of(surface_id),
                action=action,
            )
        return self._decided[key]

    async def _agent_of(self, surface_id: UUID) -> UUID | None:
        if surface_id not in self._agents:
            surface = await SourceSettings(self.uow).surface(surface_id)
            self._agents[surface_id] = surface.agent_id if surface else None
        return self._agents[surface_id]
