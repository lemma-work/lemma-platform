"""Bind identity mobile verification to the global WhatsApp surface config."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)


class GlobalWhatsAppDeliveryError(RuntimeError):
    """The shared global WhatsApp transport could not deliver a message."""


@dataclass(frozen=True, slots=True)
class GlobalWhatsAppConfiguration:
    """A redaction-safe snapshot of the existing global surface settings."""

    access_token: str | None = field(repr=False)
    phone_number_id: str | None
    display_phone_number: str | None
    app_secret: str | None = field(repr=False)
    verify_token: str | None = field(repr=False)
    webhook_security_enabled: bool


def global_whatsapp_configuration() -> GlobalWhatsAppConfiguration:
    """Return the single surface-owned configuration used by identity."""
    return GlobalWhatsAppConfiguration(
        access_token=surface_settings.whatsapp_access_token,
        phone_number_id=surface_settings.whatsapp_phone_number_id,
        display_phone_number=surface_settings.whatsapp_display_phone_number,
        app_secret=surface_settings.whatsapp_app_secret,
        verify_token=surface_settings.whatsapp_verify_token,
        webhook_security_enabled=surface_settings.surface_webhook_security_enabled,
    )


async def send_global_whatsapp_text(
    *,
    to: str,
    body: str,
    reply_to_message_id: str | None = None,
) -> bool:
    """Send identity feedback through the existing global WhatsApp transport."""
    whatsapp = global_whatsapp_configuration()
    if not whatsapp.access_token or not whatsapp.phone_number_id:
        return False
    client = WhatsAppClient(
        access_token=whatsapp.access_token,
        phone_number_id=whatsapp.phone_number_id,
    )
    try:
        await client.send_text(
            phone_number_id=whatsapp.phone_number_id,
            to=to,
            body=body,
            reply_to_message_id=reply_to_message_id,
        )
    except (httpx.HTTPError, WhatsAppApiError) as exc:
        raise GlobalWhatsAppDeliveryError from exc
    return True
