from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts import Conversation
from app.modules.agent.contracts.tools import ToolSpec
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack.tools import slack_surface_tools
from app.modules.agent_surfaces.platforms.teams.tools import teams_surface_tools
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.services.surface_send_tools import surface_send_tools
from app.modules.agent_surfaces.services.whatsapp_group_tool import (
    whatsapp_group_tools,
)
from app.modules.agent_surfaces.platforms.credential_checks import can_deliver
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
    has_native_credentials,
    native_credentials,
)

# Email, WhatsApp and Telegram are absent on purpose. Email's reply moved to the
# run observer, and the only tools WhatsApp and Telegram carried were
# `whatsapp_get_current_contact` / `telegram_get_current_chat`, which echoed
# event metadata the agent already reads off the message and cost schema tokens
# on every turn. Files flow through auto-ingest and `display_resource`.
# WhatsApp's one tool, opening a group, is added below and only in a private
# chat, because it needs the surface and a unit of work rather than credentials.
_TOOLSET_BUILDERS = {
    "SLACK": slack_surface_tools,
    "TEAMS": teams_surface_tools,
}


class SurfacePlatformToolFactory:
    """The tools a conversation's chat platform adds to its runs."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.uow_factory = uow_factory

    async def build_tools(self, *, conversation: Conversation) -> list[ToolSpec]:
        metadata = conversation.metadata or {}
        surface_type = str(metadata.get("surface_platform") or "")
        # Chat surfaces only: email's reply is sent by the observer, and an
        # unknown platform has nothing to build. A chat platform with no builder
        # of its own (WhatsApp, Telegram) still gets `surface_send_message` below.
        caps = get_platform_capabilities(surface_type)
        if caps is None or caps.is_email:
            return []
        builder = _TOOLSET_BUILDERS.get(caps.platform)

        surface_id = metadata.get("surface_id")
        if surface_id is None:
            # Conversations on system credentials carry no surface row.
            if builder is None or not has_native_credentials(surface_type):
                return []
            return list(builder(credentials=native_credentials(surface_type)))

        async with self.uow_factory() as uow:
            surface = await SurfaceRepository(uow).get(UUID(str(surface_id)))
            if surface is None:
                return []
            # One path, through the resolver, and ``prefer_native`` is the fast
            # path it used to take by hand. Calling ``native_credentials``
            # directly skipped ``_pooled_overrides``, so an agent on a surface
            # holding a pooled number was handed the *deployment's* token and
            # phone number id — and every tool it called sent from the wrong
            # number, to someone who had never seen that number before. The
            # surface still goes in, for the reason the shortcut existed:
            # Resend's ``from_address`` lives on the surface row.
            resolver = SurfaceCredentialResolver(uow=uow)
            credentials = await resolver.for_surface(
                surface, prefer_native=True, force_refresh=True
            )
            if not can_deliver(surface.surface_type, credentials):
                return []
            allow_send = surface.config.send_policy.allow_send

        tools: list[ToolSpec] = []
        if builder is not None:
            tools.extend(builder(credentials=credentials))
        if caps.bot_creates_groups and metadata.get("conversation_kind") == "DM":
            # Only in a member's own chat: see `services/whatsapp_group_tool`.
            tools.extend(
                whatsapp_group_tools(
                    uow_factory=self.uow_factory, surface_id=surface.id
                )
            )
        # The current-user surface_send_message tool, opt-in per surface and only
        # on chat surfaces (the observer sends an email surface's one reply).
        if allow_send:
            tools.extend(surface_send_tools())
        return tools
