from __future__ import annotations

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
)
from app.modules.agent_surfaces.platforms.telegram import (
    service as telegram_service_module,
)
from app.modules.agent_surfaces.platforms.whatsapp.adapter import (
    WhatsAppSurfaceAdapter,
)
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppMessageParser,
)
from app.modules.agent_surfaces.platforms.whatsapp import (
    service as whatsapp_service_module,
)
from app.modules.agent_surfaces.platforms.gmail.parser import (
    GmailMessageParser,
)
from app.modules.agent_surfaces.platforms.outlook.parser import (
    OutlookMessageParser,
)
from app.modules.agent_surfaces.platforms.telegram.adapter import (
    TelegramSurfaceAdapter,
)
from app.modules.agent_surfaces.platforms.telegram.parser import (
    TelegramMessageParser,
)


# ── WhatsApp parser ──────────────────────────────────────────────────────────


def _whatsapp_text_payload() -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "1234567890"},
                            "contacts": [
                                {
                                    "wa_id": "15550555555",
                                    "profile": {"name": "Test User"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "15550555555",
                                    "id": "wamid-test-001",
                                    "type": "text",
                                    "text": {"body": "Hello from WhatsApp"},
                                    "timestamp": "1700000000",
                                }
                            ],
                        }
                    }
                ],
            }
        ],
    }


def test_whatsapp_parse_text_message():
    parser = WhatsAppMessageParser()
    event = parser.parse(_whatsapp_text_payload())

    assert event is not None
    assert event.platform == "WHATSAPP"
    assert event.message_text == "Hello from WhatsApp"
    assert event.sender_phone == "15550555555"
    assert event.sender_display_name == "Test User"
    assert event.is_dm is True
    assert event.mentioned_agent is True
    assert event.tenant_id == "waba-001"
    assert event.external_channel_id == "1234567890"
    assert event.external_message_id == "wamid-test-001"


def test_whatsapp_parse_empty_entry():
    parser = WhatsAppMessageParser()
    assert parser.parse({}) is None
    assert parser.parse({"entry": []}) is None
    assert parser.parse({"entry": [{"changes": []}]}) is None


def test_whatsapp_parse_status_event():
    payload = _whatsapp_text_payload()
    payload["entry"][0]["changes"][0]["value"].pop("messages")
    parser = WhatsAppMessageParser()
    assert parser.parse(payload) is None


def test_whatsapp_parse_image_message():
    payload = _whatsapp_text_payload()
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    msg["type"] = "image"
    msg["image"] = {
        "id": "media-001",
        "mime_type": "image/jpeg",
        "file_size": 12345,
    }
    del msg["text"]

    parser = WhatsAppMessageParser()
    event = parser.parse(payload)
    assert event is not None
    assert event.message_text == "image"
    attachments = event.metadata.get("attachments", [])
    assert len(attachments) == 1
    assert attachments[0]["content_type"] == "image"


@pytest.mark.asyncio
async def test_whatsapp_adapter_parse():
    adapter = WhatsAppSurfaceAdapter()
    event = await adapter.parse_inbound_event(_whatsapp_text_payload())
    assert event is not None
    assert event.platform == "WHATSAPP"


@pytest.mark.asyncio
async def test_whatsapp_send_display_resource_uses_cta_url_payload(monkeypatch):
    posted: dict = {}

    class _Response:
        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, json, headers):
            posted["url"] = url
            posted["json"] = json
            posted["headers"] = headers
            return _Response()

    monkeypatch.setattr(
        whatsapp_service_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _Client(),
    )
    event = ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="waba-1",
        external_channel_id="phone-number-1",
        external_thread_id="15550555555",
        external_message_id="wamid-1",
        sender_phone="15550555555",
        message_text="show report",
        is_dm=True,
        reply_target={
            "phone_number_id": "phone-number-1",
            "sender_wa_id": "15550555555",
        },
    )

    await whatsapp_service_module.WhatsAppPlatformService(
        {
            "access_token": "wa-token",
            "api_base_url": "https://graph.example.test",
        }
    ).send_display_resource(
        event,
        SurfaceDisplayRenderPlan(
            resource_type="FILE",
            title="File: /me/report.pdf",
            summary="A file is ready to inspect.",
            actions=[
                SurfaceDisplayAction(
                    label="Open file",
                    url="https://app.example.test/pod/p/files?file=/me/report.pdf",
                )
            ],
        ),
    )

    assert posted["url"].endswith("/phone-number-1/messages")
    assert posted["headers"]["Authorization"] == "Bearer wa-token"
    assert posted["json"]["type"] == "interactive"
    assert posted["json"]["interactive"]["type"] == "cta_url"
    assert posted["json"]["interactive"]["action"]["parameters"]["url"].startswith(
        "https://app.example.test"
    )


def _telegram_text_payload() -> dict:
    return {
        "update_id": 100000001,
        "message": {
            "message_id": 42,
            "from": {
                "id": 999888777,
                "is_bot": False,
                "first_name": "Test",
                "last_name": "User",
                "username": "testuser",
            },
            "chat": {"id": 999888777, "type": "private"},
            "date": 1700000000,
            "text": "Hello from Telegram",
        },
    }


def test_telegram_parse_private_message():
    parser = TelegramMessageParser()
    event = parser.parse(_telegram_text_payload())

    assert event is not None
    assert event.platform == "TELEGRAM"
    assert event.message_text == "Hello from Telegram"
    assert event.sender_display_name == "Test User"
    assert event.is_dm is True
    assert event.mentioned_agent is True
    assert event.external_channel_id == "999888777"
    assert event.external_message_id == "42"


def test_telegram_parse_group_message():
    payload = _telegram_text_payload()
    payload["message"]["chat"] = {
        "id": -1001234567890,
        "type": "supergroup",
    }
    payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": 6}]
    payload["message"]["text"] = "/help I need help"

    parser = TelegramMessageParser()
    event = parser.parse(payload)
    assert event is not None
    assert event.is_dm is False
    assert event.mentioned_agent is True


def test_telegram_parse_empty():
    parser = TelegramMessageParser()
    assert parser.parse({}) is None
    assert parser.parse({"update_id": 1}) is None


def test_telegram_parse_photo():
    payload = _telegram_text_payload()
    payload["message"]["photo"] = [
        {"file_id": "small", "file_size": 100},
        {"file_id": "large", "file_size": 5000},
    ]
    del payload["message"]["text"]

    parser = TelegramMessageParser()
    event = parser.parse(payload)
    assert event is not None
    attachments = event.metadata.get("attachments", [])
    assert len(attachments) == 1
    assert attachments[0]["file_id"] == "large"


def test_telegram_parse_shared_contact_for_sender():
    payload = _telegram_text_payload()
    payload["message"].pop("text")
    payload["message"]["contact"] = {
        "phone_number": "+919876543210",
        "first_name": "Test",
        "user_id": 999888777,
    }

    parser = TelegramMessageParser()
    event = parser.parse(payload)

    assert event is not None
    assert event.sender_phone == "+919876543210"
    assert event.metadata["contact_shared"] is True
    assert event.metadata["contact_shared_by_sender"] is True
    assert event.metadata["shared_contact_phone"] == "+919876543210"
    assert event.message_text == ""


def test_telegram_parse_shared_contact_for_other_user_does_not_trust_phone():
    payload = _telegram_text_payload()
    payload["message"].pop("text")
    payload["message"]["contact"] = {
        "phone_number": "+919876543210",
        "first_name": "Other",
        "user_id": 123456789,
    }

    parser = TelegramMessageParser()
    event = parser.parse(payload)

    assert event is not None
    assert event.sender_phone is None
    assert event.metadata["contact_shared"] is True
    assert event.metadata["contact_shared_by_sender"] is False
    assert event.metadata["shared_contact_phone"] == "+919876543210"


@pytest.mark.asyncio
async def test_telegram_adapter_parse():
    adapter = TelegramSurfaceAdapter()
    event = await adapter.parse_inbound_event(_telegram_text_payload())
    assert event is not None
    assert event.platform == "TELEGRAM"


@pytest.mark.asyncio
async def test_telegram_send_display_resource_uses_inline_keyboard(monkeypatch):
    posted: dict = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

        @property
        def text(self):
            return ""

    async def fake_post(self, url, *, json=None, **kwargs):
        posted["url"] = url
        posted["json"] = json
        return _Response()

    # send_display_resource routes through TelegramClient.call, which uses
    # httpx.AsyncClient from the client module — patch the class method so the
    # call is intercepted regardless of import site.
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    event = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="999888777",
        external_thread_id="999888777",
        external_message_id="42",
        sender_external_user_id="999888777",
        message_text="show deals",
        is_dm=True,
        reply_target={"chat_id": "999888777", "message_id": 42},
    )

    await telegram_service_module.TelegramPlatformService(
        {"bot_token": "telegram-token", "api_base_url": "https://telegram.example/bot"}
    ).send_display_resource(
        event,
        SurfaceDisplayRenderPlan(
            resource_type="TABLE",
            title="Table: deals",
            summary="A datastore view is ready.",
            actions=[
                SurfaceDisplayAction(
                    label="Open in Lemma",
                    url="https://app.example.test/pod/p/data?tab=deals",
                )
            ],
        ),
    )

    assert posted["url"].endswith("/sendMessage")
    assert posted["json"]["parse_mode"] == "HTML"
    assert posted["json"]["reply_markup"]["inline_keyboard"][0][0]["url"].startswith(
        "https://app.example.test"
    )


async def test_telegram_download_attachment_bytes_getfile_then_download(monkeypatch):
    calls: list[str] = []

    class _Resp:
        status_code = 200

        def __init__(self, *, json_body=None, content=b""):
            self._json = json_body
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._json

        @property
        def text(self):
            return ""

    async def fake_post(self, url, *, json=None, **kwargs):
        calls.append(url)
        return _Resp(json_body={"ok": True, "result": {"file_path": "docs/a.pdf"}})

    async def fake_get(self, url, **kwargs):
        calls.append(url)
        return _Resp(content=b"PDFDATA")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    event = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="1",
        message_text="file",
    )
    result = await telegram_service_module.TelegramPlatformService(
        {"bot_token": "tok", "api_base_url": "https://telegram.example/bot"}
    ).download_attachment_bytes(
        event, {"file_id": "F1", "name": "a.pdf", "mime_type": "application/pdf"}
    )

    assert result is not None
    content, name, mime = result
    assert content == b"PDFDATA"
    assert name == "a.pdf"
    assert mime == "application/pdf"
    assert any(u.endswith("/getFile") for u in calls)


async def test_telegram_send_file_bytes_posts_multipart_document(monkeypatch):
    posted: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 7}}

        @property
        def text(self):
            return ""

    async def fake_post(self, url, *, data=None, files=None, **kwargs):
        posted["url"] = url
        posted["data"] = data
        posted["files"] = files
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    event = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="9",
        external_channel_id="9",
        message_text="x",
        reply_target={"chat_id": "9"},
    )
    sent = await telegram_service_module.TelegramPlatformService(
        {"bot_token": "tok", "api_base_url": "https://telegram.example/bot"}
    ).send_file_bytes(
        event,
        file_name="report.pdf",
        file_bytes=b"PDFDATA",
        mime_type="application/pdf",
        caption="Your report",
    )

    assert sent is True
    assert posted["url"].endswith("/sendDocument")
    assert posted["data"]["chat_id"] == "9"
    assert posted["data"]["caption"] == "Your report"
    assert posted["files"]["document"][0] == "report.pdf"


async def test_telegram_send_voice_bytes_uses_sendvoice(monkeypatch):
    posted: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 8}}

    async def fake_post(self, url, *, data=None, files=None, **kwargs):
        posted["url"] = url
        posted["data"] = data
        posted["files"] = files
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    event = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="9",
        external_channel_id="9",
        message_text="x",
        reply_target={"chat_id": "9"},
    )
    sent = await telegram_service_module.TelegramPlatformService(
        {"bot_token": "tok", "api_base_url": "https://telegram.example/bot"}
    ).send_voice_bytes(
        event,
        file_name="reply.ogg",
        audio_bytes=b"OGGOPUS",
        mime_type="audio/ogg",
    )

    assert sent is True
    # A native voice bubble requires sendVoice with the "voice" file field.
    assert posted["url"].endswith("/sendVoice")
    assert posted["files"]["voice"][0] == "reply.ogg"
    assert posted["files"]["voice"][2] == "audio/ogg"


async def test_telegram_stream_progress_send_edit_delete(monkeypatch):
    calls: list[str] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 5}}

        @property
        def text(self):
            return ""

    async def fake_post(self, url, *, json=None, **kwargs):
        calls.append(url.rsplit("/", 1)[-1])
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    event = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="9",
        external_channel_id="9",
        message_text="x",
        reply_target={"chat_id": "9"},
    )
    svc = telegram_service_module.TelegramPlatformService(
        {"bot_token": "t", "api_base_url": "https://telegram.example/bot"}
    )

    handle = await svc.stream_progress(event, "Searching the web")
    assert handle == {"message_id": 5}
    assert calls[-1] == "sendMessage"

    handle2 = await svc.stream_progress(event, "Reading results", handle)
    assert handle2 == {"message_id": 5}
    assert calls[-1] == "editMessageText"

    await svc.end_progress(event, handle2)
    assert calls[-1] == "deleteMessage"


async def test_base_adapter_download_and_send_file_defaults():
    from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter

    adapter = BaseSurfaceAdapter()
    event = ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="1",
        message_text="x",
    )
    assert (
        await adapter.download_attachment(credentials={}, event=event, attachment={})
        is None
    )
    assert (
        await adapter.send_file_attachment(
            credentials={},
            event=event,
            file_name="a.txt",
            file_bytes=b"x",
            mime_type="text/plain",
        )
        is False
    )


def test_gmail_parse_sender_string_and_attachments():
    parser = GmailMessageParser()
    event = parser.parse(
        {
            "data": {
                "thread_id": "gmail-thread-1",
                "message_id": "gmail-message-1",
                "sender": "Test User <user@example.com>",
                "to": "assistant@gmail.test",
                "subject": "Need help",
                "body_text": "Please review the file.",
                "attachments": [
                    {
                        "attachment_id": "att-1",
                        "filename": "invoice.pdf",
                        "mime_type": "application/pdf",
                        "size": 42,
                    }
                ],
            }
        }
    )

    assert event is not None
    assert event.sender_email == "user@example.com"
    assert event.sender_external_user_id == "user@example.com"
    assert event.sender_display_name == "Test User"
    assert event.external_channel_id == "assistant@gmail.test"
    assert event.metadata["attachments"][0]["id"] == "att-1"
    assert event.metadata["attachments"][0]["message_id"] == "gmail-message-1"


def test_gmail_parse_composio_payload_shape():
    parser = GmailMessageParser()
    event = parser.parse(
        {
            "id": "msg_123",
            "type": "composio.trigger.message",
            "data": {
                "thread_id": "gmail-thread-42",
                "message_id": "gmail-provider-message-42",
                "message_text": "Plain body from composio",
                "attachment_list": [
                    {
                        "attachmentId": "att-42",
                        "filename": "invite.ics",
                        "mimeType": "text/calendar",
                    }
                ],
                "payload": {
                    "headers": [
                        {
                            "name": "From",
                            "value": "Test User <user@example.com>",
                        },
                        {
                            "name": "Reply-To",
                            "value": "Replies <reply@example.com>",
                        },
                        {"name": "To", "value": "assistant@gmail.test"},
                        {"name": "Subject", "value": "Workflow Discussions"},
                        {
                            "name": "Message-ID",
                            "value": "<calendar-message@example.com>",
                        },
                    ],
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": "UGxhaW4gYm9keSBmcm9tIGNvbXBvc2lv"},
                        },
                        {
                            "mimeType": "application/ics",
                            "filename": "invite.ics",
                            "body": {"attachmentId": "att-42", "size": 1491},
                        },
                    ],
                },
            },
        }
    )

    assert event is not None
    assert event.sender_email == "user@example.com"
    assert event.external_thread_id == "gmail-thread-42"
    assert event.external_message_id == "gmail-provider-message-42"
    assert event.reply_target["recipient_email"] == "reply@example.com"
    assert event.metadata["internet_message_id"] == "<calendar-message@example.com>"
    assert event.metadata["attachments"][0]["id"] == "att-42"
    assert event.metadata["attachments"][0]["name"] == "invite.ics"


def test_outlook_parse_prefers_internet_message_id_for_dedup_and_keeps_provider_id():
    parser = OutlookMessageParser()
    event = parser.parse(
        {
            "data": {
                "id": "graph-message-1",
                "conversation_id": "outlook-thread-1",
                "internetMessageId": "<outlook-message-1@example.com>",
                "from": "Rahul <rahul@example.com>",
                "to": "assistant@outlook.test",
                "subject": "Need review",
                "body": {"contentType": "html", "content": "<p>Hello there</p>"},
                "attachments": [
                    {
                        "id": "att-1",
                        "name": "brief.txt",
                        "contentType": "text/plain",
                    }
                ],
            }
        }
    )

    assert event is not None
    assert event.sender_email == "rahul@example.com"
    assert event.external_message_id == "<outlook-message-1@example.com>"
    assert event.metadata["message_id"] == "graph-message-1"
    assert event.metadata["internet_message_id"] == "<outlook-message-1@example.com>"
    assert event.metadata["attachments"][0]["id"] == "att-1"
    assert event.metadata["attachments"][0]["message_id"] == "graph-message-1"


def test_outlook_parse_sparse_composio_trigger_payload():
    parser = OutlookMessageParser()
    event = parser.parse(
        {
            "event_type": "message.created",
            "id": "graph-message-sparse-1",
        }
    )

    assert event is not None
    assert event.external_thread_id == "graph-message-sparse-1"
    assert event.external_message_id == "graph-message-sparse-1"
    assert event.metadata["message_id"] == "graph-message-sparse-1"
    assert event.metadata["requires_message_fetch"] is True
    assert event.reply_target["message_id"] == "graph-message-sparse-1"
    assert event.sender_email is None


def test_outlook_parse_graph_message_with_recipients_and_headers():
    parser = OutlookMessageParser()
    event = parser.parse(
        {
            "id": "graph-message-1",
            "conversationId": "outlook-thread-1",
            "internetMessageId": "<outlook-message-1@example.com>",
            "from": {
                "emailAddress": {
                    "address": "rahul@example.com",
                    "name": "Rahul",
                }
            },
            "replyTo": [
                {
                    "emailAddress": {
                        "address": "reply@example.com",
                        "name": "Replies",
                    }
                }
            ],
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": "assistant@outlook.test",
                        "name": "Assistant",
                    }
                }
            ],
            "subject": "Need review",
            "body": {"contentType": "html", "content": "<p>Hello there</p>"},
            "internetMessageHeaders": [
                {"name": "In-Reply-To", "value": "<parent@example.com>"},
                {"name": "References", "value": "<parent@example.com>"},
            ],
            "attachments": [
                {
                    "id": "att-1",
                    "name": "brief.txt",
                    "contentType": "text/plain",
                }
            ],
        }
    )

    assert event is not None
    assert event.external_thread_id == "outlook-thread-1"
    assert event.reply_target["recipient_email"] == "reply@example.com"
    assert event.external_channel_id == "assistant@outlook.test"
    assert event.metadata["references"] == ["<parent@example.com>"]
    assert event.metadata["in_reply_to"] == "<parent@example.com>"
