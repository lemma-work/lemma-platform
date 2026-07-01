"""``say`` tool-coverage matrix (native voice note vs fallback egress) plus
inbound voice transcription at ingress.

Supersedes ``test_surface_voice_e2e.py``, which called
``handler.send_voice_note_for_conversation``/a fake harness directly —
bypassing the real ``say`` tool (including its real TTS synthesis call) and
the real ingress-transcription pipeline's downstream agent run. These tests
script ``say`` as a genuine LLM tool call (via ``fake_speech_provider``, a
deterministic TTS fake — only synthesis is faked, delivery runs for real).

N/A cells:
- **Only Telegram has a native voice-note send** (``sendVoice`` — see
  ``TelegramPlatformService.send_voice_note``); Slack/Teams/WhatsApp/base all
  return ``False`` from ``send_voice_note``, so ``say`` falls through to
  ``_try_send_file_attachment`` (a normal inline audio file, per
  ``send_voice_note_for_conversation``'s documented fallback chain) — Slack
  and WhatsApp support that (native files, per the display_resource matrix);
  Teams has no native file send either, so it falls all the way to a link
  card. All three fallback tiers are exercised below, just not per-platform
  redundantly.
- **Inbound voice transcription is Telegram-only** — no other platform's e2e
  fixture builder exists for a voice-message payload (WhatsApp/Slack/Teams
  audio ingestion isn't wired into these tests), matching the prior suite's
  only coverage; broadening this is a follow-up, not a silent gap introduced
  here.
- **Email is N/A for ``say``** — the agent has no ``SPEECH`` toolset on email
  surfaces in this matrix (consistent with the ask_user/request_approval
  negative pattern), and email has no audio-delivery mechanism regardless.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _load_teams_channel_mention_fixture,
    _messages_for_conversation,
    _seed_external_user,
    _set_user_mobile_number,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_say,
    script_text,
)

pytestmark = pytest.mark.e2e


_TOOL_CALL_ID = "tool-say-1"


def _telegram_voice_payload(*, message_id: int, sender_id: int) -> dict:
    """A voice-only Telegram message (no caption text)."""
    return {
        "update_id": message_id + 100000,
        "message": {
            "message_id": message_id,
            "from": {"id": sender_id, "is_bot": False, "first_name": "Surface"},
            "chat": {"id": sender_id, "type": "private"},
            "date": 1700000000,
            "voice": {
                "file_id": "voice-file-1",
                "mime_type": "audio/ogg",
                "file_size": 2048,
                "duration": 3,
            },
        },
    }


async def test_say_native_voice_note_on_telegram(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    fake_speech_provider,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    sender_id = 555050607
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
        toolsets=["SPEECH"],
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(
        text="say hello back", message_id=931, sender_id=sender_id
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[
            script_say("Hello back to you.", tool_call_id=_TOOL_CALL_ID),
            script_text("Sent!"),
        ],
    )

    voice = await wait_for_messages(message_store, "TELEGRAM_VOICE", min_count=1)
    assert voice[-1]["has_voice"] is True
    assert voice[-1]["chat_id"] == str(sender_id)


async def test_say_falls_back_to_native_file_on_slack(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    fake_speech_provider,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-say-matrix",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
        toolsets=["SPEECH"],
    )

    dm_payload = _load_slack_dm_fixture(text="say hello back", ts="1700003100.600600")
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[
            script_say("Hello back to you.", tool_call_id=_TOOL_CALL_ID),
            script_text("Sent!"),
        ],
    )

    # Slack has no native voice-note API — say falls back to a native file
    # attachment (an inline audio player), not a link.
    uploads = await wait_for_messages(
        message_store, "SLACK_FILE_UPLOAD_URL", min_count=1
    )
    assert uploads


async def test_say_falls_back_to_native_file_on_whatsapp(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    fake_speech_provider,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "WHATSAPP"},
        toolsets=["SPEECH"],
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="15550999999",
    )

    payload = _whatsapp_payload(
        text="say hello back",
        message_id="wamid-e2e-say-001",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550999999",
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[
            script_say("Hello back to you.", tool_call_id=_TOOL_CALL_ID),
            script_text("Sent!"),
        ],
    )

    uploads = await wait_for_messages(
        message_store, "WHATSAPP_MEDIA_UPLOAD", min_count=1
    )
    assert uploads


async def test_say_falls_back_to_link_card_on_teams(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    fake_speech_provider,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter

    async def _fake_bot_token(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return "teams-bot-token"

    async def _disable_graph(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return None

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)
    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _disable_graph)
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="teams",
        credentials={
            "access_token": "teams-token",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(account.id),
            "allowed_channel_ids": [REAL_TEAMS_CHANNEL_ID],
        },
        toolsets=["SPEECH"],
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[
            script_say("Hello back to you.", tool_call_id=_TOOL_CALL_ID),
            script_text("Sent!"),
        ],
    )

    # Teams has neither native voice nor native file send — say falls all the
    # way through to a link card.
    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    assert "app.example.test" in json.dumps(teams_messages)


async def test_telegram_voice_message_transcribed_at_ingress(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """An inbound voice note is transcribed at ingress; the agent's persisted
    user message is the transcript (no explicit `listen` call needed)."""
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )

    from app.modules.agent_surfaces.platforms.telegram.adapter import (
        TelegramSurfaceAdapter,
    )

    async def _fake_download(self, *, credentials, event, attachment):
        return (b"OGGOPUSAUDIO", "voice.ogg", "audio/ogg")

    monkeypatch.setattr(TelegramSurfaceAdapter, "download_attachment", _fake_download)

    import app.modules.agent.tools.speech.provider as speech_provider

    class _Result:
        text = "book a meeting with the design team tomorrow"
        detected_language = "en"
        duration_seconds = 3.0

    class _FakeTranscribeProvider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            return _Result()

    monkeypatch.setattr(
        speech_provider, "get_speech_provider", lambda: _FakeTranscribeProvider()
    )

    pod_id = test_pod["id"]
    sender_id = 555060708
    await _create_agent_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_voice_payload(message_id=932, sender_id=sender_id),
            headers={},
        ),
        script=[script_text("Sure, I'll set that up.")],
    )
    assert isinstance(context, SurfaceChatContext)

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(context.conversation_id)
    )
    user_message = next(m for m in messages if m.get("role") == "user")
    assert "book a meeting with the design team tomorrow" in user_message["text"]
