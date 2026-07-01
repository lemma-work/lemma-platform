from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Conversation
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.gmail.tools import (
    build_gmail_surface_toolset,
)
from app.modules.agent_surfaces.platforms.outlook.tools import (
    build_outlook_surface_toolset,
)
from app.modules.agent_surfaces.platforms.slack.tools import (
    build_slack_surface_toolset,
)
from app.modules.agent_surfaces.platforms.teams.tools import (
    build_teams_surface_toolset,
)
from app.modules.agent_surfaces.platforms.telegram.tools import (
    build_telegram_surface_toolset,
)
from app.modules.agent_surfaces.platforms.whatsapp.tools import (
    build_whatsapp_surface_toolset,
)
from app.modules.agent_surfaces.platforms.resend.tools import (
    build_resend_surface_toolset,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.platforms.surface_send_tools import (
    build_surface_send_toolset,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
    has_native_credentials,
    native_credentials,
)
from app.modules.connectors.api.dependencies import get_connector_service

_TOOLSET_BUILDERS = {
    "SLACK": build_slack_surface_toolset,
    "TEAMS": build_teams_surface_toolset,
    "WHATSAPP": build_whatsapp_surface_toolset,
    "TELEGRAM": build_telegram_surface_toolset,
    "GMAIL": build_gmail_surface_toolset,
    "OUTLOOK": build_outlook_surface_toolset,
    "RESEND": build_resend_surface_toolset,
}


class SurfacePlatformToolFactory:
    """Build platform-scoped toolsets for external agent conversations."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build_toolsets(
        self,
        *,
        conversation: Conversation,
    ) -> list:
        metadata = conversation.metadata or {}
        surface_type = metadata.get("surface_platform")
        builder = _TOOLSET_BUILDERS.get(str(surface_type or "").upper())
        if builder is None:
            return []

        surface_id = metadata.get("surface_id")
        if surface_id is None:
            # Conversations on system credentials carry no surface row.
            if not has_native_credentials(surface_type):
                return []
            return [builder(credentials=native_credentials(surface_type))]

        async with self.uow_factory() as uow:
            surface = await SurfaceRepository(uow).get(UUID(str(surface_id)))
            if surface is None:
                return []
            if has_native_credentials(surface.surface_type):
                credentials = native_credentials(surface.surface_type)
            else:
                resolver = SurfaceCredentialResolver(
                    session=uow.session,
                    connector_service=get_connector_service(uow),
                )
                credentials = await resolver.for_surface(surface, force_refresh=True)
            if not credentials:
                return []
            allow_send = surface.config.send_policy.allow_send

        toolsets = [builder(credentials=credentials)]
        # The current-user surface_send_message tool, opt-in per surface and only
        # on chat surfaces (email replies go through the email reply tool).
        caps = get_platform_capabilities(surface.surface_type.value)
        if allow_send and (caps is None or not caps.is_email):
            toolsets.append(build_surface_send_toolset())
        return toolsets
