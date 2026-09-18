"""``say`` coverage: how far down the delivery ladder each platform falls.

``deliver`` walks native voice note -> native file -> link card, and the
platforms differ only in how far it has to walk. That is the whole subject, so
it is a table rather than four near-identical tests: only Telegram has a native
voice send (``sendVoice``, see ``TelegramPlatformService._render_voice``);
Slack and WhatsApp have native files; Teams has neither and falls all the way
to a link. Every rung is exercised, once each, which is what the previous shape
did at four times the length.

These script ``say`` as a genuine LLM tool call, through ``fake_speech_provider``
— only synthesis is faked, delivery runs for real. That is what supersedes
``test_surface_voice_e2e.py``, which called
``handler.send_voice_note_for_conversation`` directly and so proved nothing
about the tool or the ladder.

Two cases are not rungs and keep their own tests at the bottom:

- **Email is not N/A for ``say``**, and saying it was is what hid a live bug.
  ``SPEECH`` is a per-agent declarable toolset with no platform gating, so an
  agent that has it can call ``say`` on a Resend surface. Email gets one reply,
  so the audio is held and attached to it rather than sent as a second message
  — the same branch ``display_resource`` already took, and the one ``say`` was
  never given.
- **Inbound voice transcription is Telegram-only** — no other platform's
  voice-message payload is wired into these tests. Broadening that is a
  follow-up, not a silent gap introduced here.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _messages_for_conversation,
    _resend_payload,
    _seed_external_user,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    telegram as telegram_payloads,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_say,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    CHAT_PLATFORMS,
    stage_surface,
)
from app.modules.connectors.domain.connector import AuthProvider

pytestmark = pytest.mark.e2e

_TOOL_CALL_ID = "tool-say-1"
SPOKEN = "Hello back to you."


#: Which rung of the delivery ladder each platform lands on, and where that
#: shows up in the message store. `deliver` walks native voice -> native file
#: -> link card, and the platforms differ only in how far it has to walk --
#: which is the whole subject of this matrix, so it is written as a table
#: rather than buried in four near-identical tests.
LADDER = {
    SurfacePlatform.TELEGRAM: ("a native voice note", "TELEGRAM_VOICE"),
    SurfacePlatform.SLACK: ("a native file", "SLACK_FILE_UPLOAD_URL"),
    SurfacePlatform.WHATSAPP: ("a native file", "WHATSAPP_MEDIA_UPLOAD"),
    SurfacePlatform.TEAMS: ("a link card", "TEAMS"),
}


@pytest.fixture
def platform_fake(fake_slack, fake_teams, fake_telegram, fake_whatsapp):
    return {
        SurfacePlatform.SLACK: fake_slack,
        SurfacePlatform.TEAMS: fake_teams,
        SurfacePlatform.TELEGRAM: fake_telegram,
        SurfacePlatform.WHATSAPP: fake_whatsapp,
    }


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_say_reaches_the_person_on_every_platform(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
    fake_speech_provider,
) -> None:
    """Spoken, attached or linked -- but never dropped for want of a voice API."""
    del fake_speech_provider
    rung, bucket = LADDER[platform]
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["SPEECH"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    await stage.say(
        "say hello back",
        script=[
            script_say(SPOKEN, tool_call_id=_TOOL_CALL_ID),
            script_text("Sent!"),
        ],
    )

    landed = await wait_for_messages(message_store, bucket, min_count=1)
    assert landed, f"{platform.value}: nothing arrived as {rung}"

    if platform is SurfacePlatform.TELEGRAM:
        # The only platform with a native voice-note send (`sendVoice`, see
        # `TelegramPlatformService._render_voice`); everyone else returns False
        # and falls to the next rung.
        assert landed[-1]["has_voice"] is True
        assert landed[-1]["chat_id"] == stage.sender_id
    elif platform is SurfacePlatform.TEAMS:
        # Neither native voice nor native file: it falls all the way to a link.
        assert "app.example.test" in json.dumps(landed, default=str)


# -- Email, and inbound voice: neither is a rung of that ladder ------------


async def test_say_on_resend_attaches_the_audio_to_the_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    fake_speech_provider,
    message_store,
    monkeypatch,
):
    """Email gets one reply, so audio is an attachment on it — not a lost send.

    The matrix used to call this cell N/A on the grounds that email has no
    SPEECH toolset. There is no *platform* gating on it: `SPEECH` is declared
    per agent (`toolsets=["SPEECH"]` below is the same line every other cell
    uses), so this path was reachable the whole time — and broken twice over.

    `_deliver_voice_note` returned False for email, on the reasoning that "email
    composes one reply via the reply tool; the agent attaches the audio there" —
    a tool this branch deletes. So the audio was synthesized, billed, written to
    the pod and never delivered, while `say` reported that it had spoken. Had it
    got past that, `EmailOneReplyMixin` folded five of the six envelope parts and
    never read `voice`, so the send would have carried nothing anyway.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-token",
            "api_base_url": fake_resend.api_base,
        },
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        toolsets=["SPEECH"],
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-say-1",
                text="read me the summary",
            ),
            headers={},
        ),
        script=[
            script_say("Here is the summary.", tool_call_id=_TOOL_CALL_ID),
            script_text("Sent!"),
        ],
    )

    sent = await wait_for_messages(message_store, "RESEND", min_count=1)
    carrying_audio = [message for message in sent if _audio_attachment_names(message)]
    assert carrying_audio, (
        "the run said something aloud and no email carried the audio: "
        f"{json.dumps(sent)[:600]}"
    )


def _audio_attachment_names(message: dict) -> list[str]:
    """Attachment filenames on a Resend send that look like audio."""
    attachments = message.get("attachments") or []
    names = []
    for attachment in attachments:
        name = str(
            (attachment or {}).get("filename")
            or (attachment or {}).get("file_name")
            or ""
        )
        if name.lower().endswith((".ogg", ".mp3", ".wav", ".opus")):
            names.append(name)
    return names


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
            payload=telegram_payloads.voice_note(
                message_id=932, sender_id=sender_id, file_id="voice-file-1"
            ),
            headers={},
        ),
        script=[script_text("Sure, I'll set that up.")],
    )
    assert isinstance(context, SurfaceChatContext)

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    user_message = next(m for m in messages if m.get("role") == "user")
    assert "book a meeting with the design team tomorrow" in user_message["text"]
