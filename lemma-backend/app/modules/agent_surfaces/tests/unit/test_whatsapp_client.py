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
    assert (
        resolve_api_base({"api_base_url": "http://fake/v21.0"}) == "http://fake/v21.0"
    )
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
    assert (
        classify_whatsapp_error(WhatsAppApiError(method="m", status_code=429))
        is DeliveryClassification.TRANSIENT
    )
    assert (
        classify_whatsapp_error(WhatsAppApiError(method="m", status_code=503))
        is DeliveryClassification.TRANSIENT
    )
    assert (
        classify_whatsapp_error(WhatsAppApiError(method="m", status_code=400))
        is DeliveryClassification.PERMANENT
    )
    assert (
        classify_whatsapp_error(httpx.ConnectError("boom"))
        is DeliveryClassification.TRANSIENT
    )
    assert classify_whatsapp_error(ValueError("x")) is DeliveryClassification.PERMANENT


@pytest.mark.asyncio
async def test_send_text_can_quote_the_inbound_message(monkeypatch):
    client = WhatsAppClient(access_token="t", phone_number_id="phone-1")
    calls: list[dict] = []

    async def _capture(*, phone_number_id, payload):
        calls.append({"phone_number_id": phone_number_id, "payload": payload})
        return "wamid.out"

    monkeypatch.setattr(client, "send_message_payload", _capture)

    message_id = await client.send_text(
        phone_number_id="phone-1",
        to="14155552671",
        body="Mobile number verified",
        reply_to_message_id="wamid.in",
    )

    assert message_id == "wamid.out"
    assert calls == [
        {
            "phone_number_id": "phone-1",
            "payload": {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": "14155552671",
                "type": "text",
                "text": {
                    "body": "Mobile number verified",
                    "preview_url": False,
                },
                "context": {"message_id": "wamid.in"},
            },
        }
    ]


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
        {
            "access_token": "t",
            "phone_number_id": "phone-1",
            "api_base_url": "http://x/v21.0",
        }
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
        {
            "access_token": "t",
            "phone_number_id": "phone-1",
            "api_base_url": "http://x/v21.0",
        }
    )
    calls: list[dict] = []

    async def _capture(*, phone_number_id, payload):
        calls.append(payload)
        return None

    monkeypatch.setattr(service._client, "send_message_payload", _capture)

    # No inbound message id → nothing to mark read / react to; must not raise.
    await service.add_processing_indicator(_inbound_event(message_id=None))
    assert calls == []


# --- outbound message bodies ----------------------------------------------


def _service() -> WhatsAppPlatformService:
    return WhatsAppPlatformService(
        {
            "access_token": "t",
            "phone_number_id": "phone-1",
            "api_base_url": "http://x/v21.0",
        }
    )


@pytest.mark.asyncio
async def test_a_long_answer_is_split_rather_than_dropped(monkeypatch):
    """4096 is Meta's hard ceiling, and an oversized body is rejected outright.

    The person got nothing at all, which is worse than the truncation the other
    send path did.
    """
    service = _service()
    bodies: list[str] = []

    async def _capture(*, phone_number_id, payload):
        bodies.append(payload["text"]["body"])
        return "wamid.out"

    monkeypatch.setattr(service._client, "send_message_payload", _capture)

    paragraph = "word " * 400  # ~2000 characters
    await service.send_message(
        _inbound_event(message_id="wamid.in-1"), "\n\n".join([paragraph] * 4)
    )

    assert len(bodies) > 1
    assert all(len(body) <= 4096 for body in bodies)
    assert "".join(bodies).count("word") == 1600


@pytest.mark.asyncio
async def test_a_split_never_leaves_half_a_bold_pair(monkeypatch):
    service = _service()
    bodies: list[str] = []

    async def _capture(*, phone_number_id, payload):
        bodies.append(payload["text"]["body"])
        return "wamid.out"

    monkeypatch.setattr(service._client, "send_message_payload", _capture)

    filler = "word " * 900  # pushes the bold run over the boundary
    await service.send_message(
        _inbound_event(message_id="wamid.in-1"), f"{filler}\n\n**Conclusion** here"
    )

    for body in bodies:
        assert body.count("*") % 2 == 0


@pytest.mark.asyncio
async def test_progress_is_posted_as_its_own_message(monkeypatch):
    """WhatsApp cannot edit a message, so an update can only be a new one."""
    service = _service()
    payloads: list[dict] = []

    async def _capture(*, phone_number_id, payload):
        payloads.append(payload)
        return "wamid.progress"

    monkeypatch.setattr(service._client, "send_message_payload", _capture)

    await service.stream_progress(
        _inbound_event(message_id="wamid.in-1"),
        "Working on it — 1 of 2 steps done.\n✅ Pull the numbers",
    )

    assert len(payloads) == 1
    assert payloads[0]["type"] == "text"
    assert payloads[0]["text"]["preview_url"] is False
    assert "✅ Pull the numbers" in payloads[0]["text"]["body"]


@pytest.mark.asyncio
async def test_a_typing_refresh_does_not_re_post_the_reaction(monkeypatch):
    """The bubble is refreshed on a timer; the acknowledgement is sent once.

    Without the distinction, a rejected read/typing call would drop into the
    reaction fallback on every tick — an API call every twenty seconds saying
    something already said.
    """
    service = _service()
    calls: list[dict] = []

    async def _refuse(*, phone_number_id, message_id):
        raise RuntimeError("read receipts unavailable")

    async def _capture_reaction(**kwargs):
        calls.append(kwargs)
        return "wamid.reaction"

    monkeypatch.setattr(service._client, "mark_read_and_typing", _refuse)
    monkeypatch.setattr(service._client, "react", _capture_reaction)

    event = _inbound_event(message_id="wamid.in-1")
    await service.add_processing_indicator(event)
    assert len(calls) == 1

    await service.add_processing_indicator(event, {"is_refresh": True})
    assert len(calls) == 1
