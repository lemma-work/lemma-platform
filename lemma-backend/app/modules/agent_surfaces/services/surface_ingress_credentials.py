"""Credentials for a reply, resolved from whatever context is to hand.

Small and separate because the answer differs by platform: Resend needs the
surface row for its ``from_address`` where the others do not.
"""

from __future__ import annotations

from typing import Any


from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

_CONVERSATION_TITLE_MAX_LENGTH = 120
# Recent thread/channel messages fetched per run for group-mention continuity.
_CHANNEL_CONTEXT_LIMIT = 15


class SurfaceIngressCredentialMixin:
    async def _resolve_credentials(
        self,
        surface: AgentSurfaceEntity,
    ) -> dict[str, Any]:
        return await self.credential_resolver.for_surface(surface)

    async def _resolve_credentials_from_context(
        self, context: AgentSurfaceContext
    ) -> dict[str, Any]:
        """Credentials for a reply that only has the run's context in hand.

        Resolved *for the surface* when the context names one: ``for_platform``
        cannot know a value that lives on the surface row, and every agent reply
        on an email surface failed for want of it.
        """
        needs_surface = self._credentials_need_surface(context)
        if self._uow_factory is not None:
            # Worker path: read credentials in a short UoW and return the plain
            # dict, so no connection is held during the platform I/O that follows.
            async with self._uow_factory() as uow:
                resolver = SurfaceCredentialResolver(
                    session=uow.session,
                    connector_service=self._connector_service_factory(uow),
                )
                if needs_surface:
                    surface = await SurfaceRepository(uow).get(context.surface_id)
                    if surface is not None:
                        return await resolver.for_surface(surface)
                return await resolver.for_platform(
                    context.platform, context.surface_account_id, surface=None
                )
        if needs_surface:
            surface = await self.surface_repository.get(context.surface_id)
            if surface is not None:
                return await self.credential_resolver.for_surface(surface)
        return await self.credential_resolver.for_platform(
            context.platform, context.surface_account_id, surface=None
        )

    @staticmethod
    def _credentials_need_surface(context: AgentSurfaceContext) -> bool:
        """Resend's ``from_address`` lives on the surface row; nothing else does,
        so the extra read stays off every other platform's inbound path."""
        return (
            context.surface_id is not None
            and str(context.platform or "").upper() == SurfacePlatform.RESEND.value
        )
