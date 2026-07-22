"""Bind identity mobile verification to the global WhatsApp surface config."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.agent_surfaces.config import surface_settings


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
