"""The account a surface speaks through, as channels key its places.

A Slack app in a workspace, a Teams bot in a tenant, a Telegram bot, a
WhatsApp number, an inbox: the thing on the platform that outlives one
installation of the bot. A place heard by a bot that was removed and installed
again is still in the same account, so its channel keeps its history.

Slack and Teams are keyed by the workspace (tenant) *and* the bot: two agents'
apps installed in one workspace are two accounts, each with its own places,
token and history -- one row between them had the last app to speak take the
other's places over. A reinstall of the same app keeps its bot id, so it keeps
its account. A surface that has not recorded its bot yet has no bot in its
key; the account is given the bot when the surface learns it
(``channels``' ``AccountRepository.account_for``), so nothing is split.

A WhatsApp number is keyed by its phone-number id, so a surface made again for
the same number finds its groups. Only a surface whose platform names no such
account is its own.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.channels.contracts import AccountKey


def account_of(surface: AgentSurfaceEntity) -> AccountKey:
    """The account a surface speaks through, as its platform names it."""
    platform = surface.surface_type
    bot = (surface.surface_identity_id or "").strip() or None
    if platform is SurfacePlatform.SLACK and surface.external_workspace_id:
        return AccountKey(workspace=surface.external_workspace_id, bot=bot)
    if platform is SurfacePlatform.TEAMS and surface.external_tenant_id:
        return AccountKey(workspace=surface.external_tenant_id, bot=bot)
    if platform in (SurfacePlatform.TELEGRAM, SurfacePlatform.WHATSAPP) and bot:
        # The bot (the phone-number id) is the whole account.
        return AccountKey(workspace=bot)
    if platform is SurfacePlatform.RESEND and surface.surface_identity_email:
        return AccountKey(workspace=surface.surface_identity_email.strip().lower())
    return AccountKey(workspace=f"surface:{surface.id}")
