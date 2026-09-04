from __future__ import annotations

import httpx
import pytest

from app.modules.agent_surfaces.contracts.whatsapp import (
    GlobalWhatsAppDeliveryError,
    send_global_whatsapp_text,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppClient


@pytest.mark.asyncio
async def test_global_whatsapp_feedback_reuses_surface_credentials(monkeypatch):
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "surface-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "global-phone")
    calls: list[dict] = []

    async def _capture(self, **kwargs):
        calls.append(
            {
                "access_token": self._access_token,
                "configured_phone": self._phone_number_id,
                **kwargs,
            }
        )
        return "wamid.out"

    monkeypatch.setattr(WhatsAppClient, "send_text", _capture)

    assert await send_global_whatsapp_text(
        to="14155552671",
        body="Mobile number verified",
        reply_to_message_id="wamid.in",
    )
    assert calls == [
        {
            "access_token": "surface-token",
            "configured_phone": "global-phone",
            "phone_number_id": "global-phone",
            "to": "14155552671",
            "body": "Mobile number verified",
            "reply_to_message_id": "wamid.in",
        }
    ]


@pytest.mark.asyncio
async def test_global_whatsapp_feedback_wraps_transport_failures(monkeypatch):
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "surface-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "global-phone")

    async def _fail(self, **kwargs):
        del self, kwargs
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(WhatsAppClient, "send_text", _fail)

    with pytest.raises(GlobalWhatsAppDeliveryError):
        await send_global_whatsapp_text(
            to="14155552671",
            body="Mobile number verified",
            reply_to_message_id="wamid.in",
        )
