"""Whether a platform's credentials can send anything at all.

One entry per platform rather than a branch per platform in the service that
asks: the fallback reply needs to know before it tries, so a surface whose
account has lost its token is named in a warning instead of failing on every
stranger who writes to it.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import reveal_secret
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    ResendCredentials,
    SlackCredentials,
    SurfaceCredentials,
    TelegramCredentials,
    WhatsAppCredentials,
    credentials_model_for,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform


def _teams_bot_configured(_: SurfaceCredentials) -> bool:
    # Teams sends as the deployment's bot, whatever the account holds.
    return bool(
        surface_settings.microsoft_bot_app_id
        and reveal_secret(surface_settings.microsoft_bot_app_password)
    )


_CAN_DELIVER: dict[SurfacePlatform, Callable[[SurfaceCredentials], bool]] = {
    SurfacePlatform.SLACK: lambda c: (
        isinstance(c, SlackCredentials) and c.token is not None
    ),
    SurfacePlatform.TELEGRAM: lambda c: (
        isinstance(c, TelegramCredentials) and c.bot_token is not None
    ),
    SurfacePlatform.WHATSAPP: lambda c: (
        isinstance(c, WhatsAppCredentials) and c.access_token is not None
    ),
    SurfacePlatform.RESEND: lambda c: (
        isinstance(c, ResendCredentials) and c.api_key is not None
    ),
    SurfacePlatform.TEAMS: _teams_bot_configured,
}


def can_deliver(platform: SurfacePlatform | str, credentials: CredentialsLike) -> bool:
    """Whether ``credentials`` could send a message on ``platform`` at all."""
    resolved = SurfacePlatform(str(platform).upper())
    typed = credentials_model_for(resolved).of(credentials)
    return _CAN_DELIVER[resolved](typed)
