"""The pod-wide WhatsApp transport, for a module that is not a surface.

`identity` verifies a mobile number by sending a WhatsApp message, and the only
WhatsApp credentials and client in the platform are this module's. It was
`app/composition/identity_whatsapp.py`, which put `agent_surfaces.config` and
`agent_surfaces.platforms.whatsapp.client` into `identity`'s build for one send
and one settings read.

`GlobalWhatsAppConfiguration` is a snapshot rather than the settings object: it
names the six fields identity reads -- all six are read -- and keeps the three
secrets out of a repr. Publishing `surface_settings` itself would have made
every future WhatsApp setting part of this surface by default.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
platform client.
"""

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
