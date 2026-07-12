"""Deterministic guards for the typed WhatsApp client + read/typing indicator."""

from __future__ import annotations

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.delivery import DeliveryClassification
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
    classify_whatsapp_error,
    resolve_api_base,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)


# --- base resolution + envelope parsing -----------------------------------

def test_resolve_api_base_prefers_credential_override():
    assert resolve_api_base({"api_base_url": "http://fake/v21.0"}) == "http://fake/v21.0"
    assert resolve_api_base({}) == "https://graph.facebook.com/v21.0"
    assert resolve_api_base(None) == "https://graph.facebook.com/v21.0"


def test_parse_returns_dict_on_2xx():
    client = WhatsAppClient(access_token="t", api_base="http://x/v21.0")
    response = httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
    data = client._parse(response, method="messages")
    assert data["messages"][0]["id"] == "wamid.1"


def test_parse_raises_with_body_excerpt_on_error():
    client = WhatsAppClient(access_token="t", api_base="http://x/v21.0")
    response = httpx.Response(400, text="bad request: invalid recipient")
    with pytest.raises(WhatsAppApiError) as exc_info:
        client._parse(response, method="messages")
    err = exc_info.value
    assert err.status_code == 400
    assert "invalid recipient" in (err.body_excerpt or "")


def test_classify_whatsapp_error():
    assert classify_whatsapp_error(
        WhatsAppApiError(method="m", status_code=429)
    ) is DeliveryClassification.TRANSIENT
    assert classify_whatsapp_error(
        WhatsAppApiError(method="m", status_code=503)
    ) is DeliveryClassification.TRANSIENT
    assert classify_whatsapp_error(
        WhatsAppApiError(method="m", status_code=400)
    ) is DeliveryClassification.PERMANENT
    assert classify_whatsapp_error(
        httpx.ConnectError("boom")
    ) is DeliveryClassification.TRANSIENT
    assert classify_whatsapp_error(ValueError("x")) is DeliveryClassification.PERMANENT


# --- read receipt + typing indicator --------------------------------------

def _inbound_event(*, message_id: str | None) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="phone-1",
        external_thread_id="15551234567@phone-1",
        external_message_id=message_id,
        sender_external_user_id="15551234567",
        sender_phone="15551234567",
        message_text="hi",
        reply_target={"phone_number_id": "phone-1", "sender_wa_id": "15551234567"},
    )


@pytest.mark.asyncio
async def test_add_processing_indicator_marks_read_and_typing(monkeypatch):
    service = WhatsAppPlatformService(
        {"access_token": "t", "phone_number_id": "phone-1", "api_base_url": "http://x/v21.0"}
    )
    calls: list[dict] = []

    async def _capture(*, phone_number_id, payload):
        calls.append({"phone_number_id": phone_number_id, **payload})
        return "wamid.ack"

    monkeypatch.setattr(service._client, "send_message_payload", _capture)

    await service.add_processing_indicator(_inbound_event(message_id="wamid.in-1"))

    assert len(calls) == 1
    call = calls[0]
    assert call["status"] == "read"
    assert call["message_id"] == "wamid.in-1"
    assert call["typing_indicator"] == {"type": "text"}
    assert call["phone_number_id"] == "phone-1"


@pytest.mark.asyncio
async def test_add_processing_indicator_noop_without_message_id(monkeypatch):
    service = WhatsAppPlatformService(
        {"access_token": "t", "phone_number_id": "phone-1", "api_base_url": "http://x/v21.0"}
    )
    calls: list[dict] = []

    async def _capture(*, phone_number_id, payload):
        calls.append(payload)
        return None

    monkeypatch.setattr(service._client, "send_message_payload", _capture)

    # No inbound message id → nothing to mark read / react to; must not raise.
    await service.add_processing_indicator(_inbound_event(message_id=None))
    assert calls == []
