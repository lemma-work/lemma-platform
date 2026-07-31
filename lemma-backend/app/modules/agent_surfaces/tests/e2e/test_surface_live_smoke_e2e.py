from __future__ import annotations

import os

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

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.provider,
    pytest.mark.surface_live,
    pytest.mark.skipif(
        os.getenv("LEMMA_RUN_SURFACE_LIVE_E2E") != "1",
        reason="Set LEMMA_RUN_SURFACE_LIVE_E2E=1 to run live surface smoke tests.",
    ),
]


def _required_env(*names: str) -> dict[str, str]:
    values = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"Missing live surface env: {', '.join(missing)}")
    return values


def _event(
    platform: str,
    *,
    target: dict,
    thread_id: str = "surface-live-smoke",
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id=str(target.get("channel") or target.get("chat_id") or ""),
        external_thread_id=thread_id,
        external_message_id=f"{thread_id}-message",
        sender_external_user_id=str(target.get("sender") or "surface-live-user"),
        sender_email=target.get("sender_email"),
        sender_phone=target.get("sender_phone"),
        sender_display_name="Surface Live Smoke",
        message_text="live smoke",
        is_dm=True,
        mentioned_agent=True,
        should_start_conversation=True,
        reply_target=target,
    )


async def test_live_telegram_minimal_send():
    env = _required_env("TELEGRAM_BOT_TOKEN", "LEMMA_SURFACE_LIVE_TELEGRAM_CHAT_ID")
    service = TelegramPlatformService({"bot_token": env["TELEGRAM_BOT_TOKEN"]})

    await service.send_message(
        event=_event(
            "TELEGRAM",
            target={"chat_id": env["LEMMA_SURFACE_LIVE_TELEGRAM_CHAT_ID"]},
        ),
        message="Lemma surface live smoke: Telegram",
    )


async def test_live_whatsapp_minimal_send():
    env = _required_env(
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "LEMMA_SURFACE_LIVE_WHATSAPP_TO",
    )
    service = WhatsAppPlatformService(
        {
            "access_token": env["WHATSAPP_ACCESS_TOKEN"],
            "phone_number_id": env["WHATSAPP_PHONE_NUMBER_ID"],
        }
    )

    await service.send_message(
        event=_event(
            "WHATSAPP",
            target={
                "phone_number_id": env["WHATSAPP_PHONE_NUMBER_ID"],
                "sender_wa_id": env["LEMMA_SURFACE_LIVE_WHATSAPP_TO"],
            },
        ),
        message="Lemma surface live smoke: WhatsApp",
    )


async def test_live_slack_minimal_send():
    env = _required_env(
        "LEMMA_SURFACE_LIVE_SLACK_BOT_TOKEN",
        "LEMMA_SURFACE_LIVE_SLACK_CHANNEL_ID",
    )
    service = SlackPlatformService(
        credentials={"access_token": env["LEMMA_SURFACE_LIVE_SLACK_BOT_TOKEN"]}
    )

    await service.send_message(
        event=_event(
            "SLACK",
            target={"channel": env["LEMMA_SURFACE_LIVE_SLACK_CHANNEL_ID"]},
        ),
        message="Lemma surface live smoke: Slack",
    )


async def test_live_teams_minimal_send():
    env = _required_env(
        "LEMMA_SURFACE_LIVE_TEAMS_TENANT_ID",
        "LEMMA_SURFACE_LIVE_TEAMS_SERVICE_URL",
        "LEMMA_SURFACE_LIVE_TEAMS_CONVERSATION_ID",
    )
    adapter = TeamsSurfaceAdapter()

    await adapter.send_message(
        credentials={},
        event=_event(
            "TEAMS",
            target={
                "service_url": env["LEMMA_SURFACE_LIVE_TEAMS_SERVICE_URL"],
                "conversation_id": env["LEMMA_SURFACE_LIVE_TEAMS_CONVERSATION_ID"],
            },
        ).model_copy(update={"tenant_id": env["LEMMA_SURFACE_LIVE_TEAMS_TENANT_ID"]}),
        message="Lemma surface live smoke: Teams",
    )


async def test_live_gmail_minimal_send():
    env = _required_env(
        "LEMMA_SURFACE_LIVE_GMAIL_ACCESS_TOKEN",
        "LEMMA_SURFACE_LIVE_EMAIL_TO",
    )
    service = GmailPlatformService(
        {"access_token": env["LEMMA_SURFACE_LIVE_GMAIL_ACCESS_TOKEN"]}
    )

    await service.send_message(
        event=_event(
            "GMAIL",
            target={
                "recipient_email": env["LEMMA_SURFACE_LIVE_EMAIL_TO"],
                "subject": "Lemma surface live smoke",
            },
        ),
        message="Lemma surface live smoke: Gmail",
    )


async def test_live_outlook_minimal_reply():
    env = _required_env(
        "LEMMA_SURFACE_LIVE_OUTLOOK_ACCESS_TOKEN",
        "LEMMA_SURFACE_LIVE_OUTLOOK_MESSAGE_ID",
    )
    service = OutlookPlatformService(
        {"access_token": env["LEMMA_SURFACE_LIVE_OUTLOOK_ACCESS_TOKEN"]}
    )

    await service.send_message(
        event=_event(
            "OUTLOOK",
            target={"message_id": env["LEMMA_SURFACE_LIVE_OUTLOOK_MESSAGE_ID"]},
        ),
        message="Lemma surface live smoke: Outlook",
    )


async def test_live_resend_minimal_send():
    env = _required_env(
        "RESEND_API_KEY",
        "LEMMA_SURFACE_LIVE_RESEND_FROM",
        "LEMMA_SURFACE_LIVE_EMAIL_TO",
    )
    service = ResendPlatformService(
        {
            "api_key": env["RESEND_API_KEY"],
            "from_address": env["LEMMA_SURFACE_LIVE_RESEND_FROM"],
            "from_name": "Lemma",
        }
    )

    await service.send_message(
        event=_event(
            "RESEND",
            target={
                "recipient_email": env["LEMMA_SURFACE_LIVE_EMAIL_TO"],
                "subject": "Lemma surface live smoke",
            },
        ),
        message="Lemma surface live smoke: Resend",
    )
