from __future__ import annotations

import base64
from email import message_from_bytes

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.gmail.service import GmailPlatformService
from app.modules.agent_surfaces.platforms.outlook.service import OutlookPlatformService
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
from app.modules.agent_surfaces.platforms.telegram.service import TelegramPlatformService
from app.modules.agent_surfaces.platforms.whatsapp.service import WhatsAppPlatformService
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages

pytestmark = pytest.mark.e2e


def _slack_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="T-contract",
        external_channel_id="D-contract",
        external_thread_id="1700000000.000001",
        external_message_id="1700000000.000001",
        sender_external_user_id="U-contract",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"channel": "D-contract", "thread_ts": "1700000000.000001"},
    )


def _teams_event(fake_teams) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="tenant-contract",
        external_channel_id="19:channel",
        external_thread_id="activity-root",
        external_message_id="activity-reply",
        sender_external_user_id="29:user",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=False,
        mentioned_agent=True,
        reply_target={
            "service_url": fake_teams.service_url,
            "conversation_id": "conversation-contract",
            "reply_to_id": "activity-root",
        },
    )


def _telegram_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="424242",
        external_thread_id="424242",
        external_message_id="111",
        sender_external_user_id="424242",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"chat_id": "424242", "message_id": 111},
    )


def _whatsapp_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="waba-contract",
        external_channel_id="phone-contract",
        external_thread_id="15551234567@phone-contract",
        external_message_id="wamid.contract",
        sender_external_user_id="15551234567",
        sender_phone="15551234567",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=True,
        mentioned_agent=True,
        reply_target={
            "phone_number_id": "phone-contract",
            "sender_wa_id": "15551234567",
        },
    )


def _gmail_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="GMAIL",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="gmail-thread-1",
        external_message_id="gmail-message-1",
        sender_external_user_id="sender@example.test",
        sender_email="sender@example.test",
        sender_display_name="Sender",
        message_text="hello",
        should_start_conversation=True,
        reply_target={
            "recipient_email": "sender@example.test",
            "subject": "Contract Subject",
            "thread_id": "gmail-thread-1",
            "in_reply_to": "<gmail-message-1@example.test>",
            "references": ["<gmail-root@example.test>"],
        },
    )


def _outlook_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="OUTLOOK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="outlook-thread-1",
        external_message_id="outlook-message-1",
        sender_external_user_id="sender@example.test",
        sender_email="sender@example.test",
        sender_display_name="Sender",
        message_text="hello",
        should_start_conversation=True,
        reply_target={"message_id": "outlook-message-1"},
    )


def _resend_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="RESEND",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="resend-thread-1",
        external_message_id="resend-message-1",
        sender_external_user_id="sender@example.test",
        sender_email="sender@example.test",
        sender_display_name="Sender",
        message_text="hello",
        should_start_conversation=True,
        reply_target={
            "recipient_email": "sender@example.test",
            "subject": "Contract Subject",
            "in_reply_to": "<resend-message-1@example.test>",
            "references": ["<resend-root@example.test>"],
        },
    )


async def test_slack_final_answer_contract(fake_slack, message_store):
    service = SlackPlatformService(
        credentials={
            "access_token": "xoxb-contract",
            "scope": "chat:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
        }
    )

    await service.send_message(
        event=_slack_event(),
        message="*Contract* reply",
        metadata={"agent_display_name": "Contract Agent"},
    )

    messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/api/chat.postMessage"
    assert payload["_authorization"] == "Bearer xoxb-contract"
    assert payload["channel"] == "D-contract"
    assert payload["thread_ts"] == "1700000000.000001"
    assert payload["text"] == "*Contract* reply"
    assert payload["username"] == "Contract Agent"


async def test_teams_final_answer_contract(fake_teams, message_store, monkeypatch):
    adapter = TeamsSurfaceAdapter()

    async def _fake_bot_token(self, tenant_id=None):
        assert tenant_id == "tenant-contract"
        return "teams-contract-token"

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)

    await adapter.send_message(
        credentials={},
        event=_teams_event(fake_teams),
        message="**Contract** reply",
    )

    messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_authorization"] == "Bearer teams-contract-token"
    assert payload["_path"] == "/teams/v3/conversations/conversation-contract/activities"
    assert payload["body"] == {
        "type": "message",
        "text": "**Contract** reply",
        "textFormat": "markdown",
        "replyToId": "activity-root",
    }


async def test_telegram_final_answer_contract_and_retry(fake_telegram, message_store):
    service = TelegramPlatformService(
        {
            "bot_token": "telegram-contract-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        }
    )
    fake_telegram.fail_next["sendMessage"] = 1

    await service.send_message(
        event=_telegram_event(),
        message="Contract *reply*",
    )

    messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/bottelegram-contract-token/sendMessage"
    assert payload["chat_id"] == "424242"
    assert payload["reply_parameters"] == {
        "message_id": 111,
        "allow_sending_without_reply": True,
    }
    assert payload["parse_mode"] == "MarkdownV2"
    assert "Contract" in payload["text"]


async def test_whatsapp_final_answer_contract(fake_whatsapp, message_store):
    service = WhatsAppPlatformService(
        {
            "access_token": "wa-contract-token",
            "phone_number_id": "phone-contract",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        }
    )

    await service.send_message(event=_whatsapp_event(), message="Contract reply")

    messages = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/v21.0/phone-contract/messages"
    assert payload["_authorization"] == "Bearer wa-contract-token"
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "15551234567"
    assert payload["type"] == "text"
    assert payload["text"] == {"body": "Contract reply"}


async def test_chat_surfaces_skip_outbound_when_credentials_are_missing(
    fake_slack,
    fake_whatsapp,
    message_store,
):
    slack = SlackPlatformService(
        credentials={
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
        }
    )
    whatsapp = WhatsAppPlatformService(
        {
            "phone_number_id": "phone-contract",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        }
    )

    await slack.send_message(event=_slack_event(), message="should not send")
    await whatsapp.send_message(event=_whatsapp_event(), message="should not send")

    assert message_store.get_all("SLACK") == []
    assert message_store.get_all("WHATSAPP") == []


async def test_gmail_final_answer_contract(fake_gmail, message_store):
    service = GmailPlatformService(
        {
            "access_token": "gmail-contract-token",
            "api_base_url": fake_gmail.api_base,
        }
    )

    await service.send_message(event=_gmail_event(), message="Contract reply")

    messages = await wait_for_messages(message_store, "GMAIL", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/gmail/v1/users/me/messages/send"
    assert payload["_authorization"] == "Bearer gmail-contract-token"
    assert payload["threadId"] == "gmail-thread-1"

    raw = payload["raw"]
    padding = "=" * (-len(raw) % 4)
    email = message_from_bytes(base64.urlsafe_b64decode(raw + padding))
    assert email["To"] == "sender@example.test"
    assert email["Subject"] == "Re: Contract Subject"
    assert email["In-Reply-To"] == "<gmail-message-1@example.test>"
    assert email["References"] == "<gmail-root@example.test>"
    assert "Contract reply" in email.get_payload()


async def test_outlook_final_answer_contract(fake_outlook, message_store):
    service = OutlookPlatformService(
        {
            "access_token": "outlook-contract-token",
            "api_base_url": fake_outlook.api_base,
        }
    )

    await service.send_message(event=_outlook_event(), message="Contract reply")

    messages = await wait_for_messages(message_store, "OUTLOOK_REPLY", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/v1.0/me/messages/outlook-message-1/reply"
    assert payload["_authorization"] == "Bearer outlook-contract-token"
    assert payload["message_id"] == "outlook-message-1"
    assert payload["body"] == {
        "message": {
            "body": {
                "contentType": "Text",
                "content": "Contract reply",
            }
        }
    }


async def test_resend_final_answer_contract(fake_resend, message_store):
    service = ResendPlatformService(
        {
            "api_key": "resend-contract-token",
            "from_address": "assistant@example.test",
            "from_name": "Lemma Contract",
            "api_base_url": fake_resend.api_base,
        }
    )

    await service.send_message(event=_resend_event(), message="Contract reply")

    messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/emails"
    assert payload["_authorization"] == "Bearer resend-contract-token"
    assert payload["from"] == "Lemma Contract <assistant@example.test>"
    assert payload["to"] == ["sender@example.test"]
    assert payload["subject"] == "Re: Contract Subject"
    assert payload["headers"] == {
        "In-Reply-To": "<resend-message-1@example.test>",
        "References": "<resend-root@example.test>",
    }
    assert payload["text"] == "Contract reply"
