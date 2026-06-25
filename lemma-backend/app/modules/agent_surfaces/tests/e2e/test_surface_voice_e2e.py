"""E2E: inbound voice transcription at ingress (D) and `say` native voice
note egress (E), exercised over the Telegram surface with the fake Bot API."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    EmulatedAgentHarness,
    _create_surface,
    _process_ingress_and_emulate_reply,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_harnesses import RecordPromptHarness

pytestmark = pytest.mark.e2e


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


def _wire_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


async def test_telegram_voice_message_transcribed_at_ingress(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """An inbound voice note is transcribed at ingress; the agent receives the
    transcript as the user's words (no manual `listen` call needed)."""
    _wire_telegram(monkeypatch, fake_telegram)

    # The platform download (Bot API getFile) and the speech provider are the only
    # external pieces faked; everything else is the real pipeline.
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

    class _Provider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            return _Result()

    monkeypatch.setattr(speech_provider, "get_speech_provider", lambda: _Provider())

    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="222333444",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    harness = RecordPromptHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_voice_payload(message_id=77, sender_id=222333444),
            headers={},
        ),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)

    # The agent's user prompt is the transcript of the voice note.
    assert harness.prompts, "agent run did not record a prompt"
    assert "book a meeting with the design team tomorrow" in harness.prompts[0]


async def test_say_delivers_native_voice_note_on_telegram(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A pod audio file delivered via the say egress path becomes a native
    Telegram voice note (sendVoice), not a generic document."""
    from app.core.authorization.current import (
        reset_current_context,
        set_current_context,
    )
    from app.core.authorization.factory import create_authorization_data_service
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )
    from app.modules.datastore.api.dependencies import build_file_service

    _wire_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="333444555",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    # Establish a conversation + link (the egress target for the voice note).
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="say hello back", message_id=88, sender_id=333444555
            ),
            headers={},
        ),
        EmulatedAgentHarness(),
    )
    assert isinstance(context, SurfaceChatContext)

    # Seed a pod audio file (what `say` would synthesize + save).
    uow = SqlAlchemyUnitOfWork(db_session)
    auth_ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=context.user_id, pod_id=context.pod_id
    )
    token = set_current_context(auth_ctx)
    try:
        entity = await build_file_service(uow).create_file(
            pod_id=context.pod_id,
            name="reply.ogg",
            file_content=b"OGGOPUSVOICE",
            ctx=auth_ctx,
            directory_path="/me/speech",
            search_enabled=False,
        )
        await uow.commit()
    finally:
        reset_current_context(token)

    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))
    delivered = await handler.send_voice_note_for_conversation(
        conversation_id=context.conversation_id, path=entity.path
    )
    assert delivered is True

    # Delivered via sendVoice (TELEGRAM_VOICE) — a native voice bubble, not a
    # sendDocument/sendAudio fallback.
    voice = await wait_for_messages(message_store, "TELEGRAM_VOICE", min_count=1)
    assert voice[-1]["has_voice"] is True
    assert voice[-1]["chat_id"] == "333444555"
