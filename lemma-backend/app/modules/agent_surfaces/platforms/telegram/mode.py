"""Telegram delivery-mode resolution.

Telegram can receive updates two ways: a public HTTPS webhook (the default) or a
native long-poll receiver (``ENABLE_TELEGRAM_POLLING_MODE``, for runtimes
without a public URL). The webhook-vs-polling decision was previously duplicated
across the surface service and controller; centralize it here.
"""

from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)


def telegram_polling_enabled() -> bool:
    return bool(surface_settings.enable_telegram_polling_mode)


def telegram_requires_webhook_setup(surface: AgentSurfaceEntity) -> bool:
    """True when this surface needs a Telegram ``setWebhook`` registration."""
    return (
        surface.surface_type is SurfacePlatform.TELEGRAM
        and surface.account_id is not None
        and not telegram_polling_enabled()
    )
