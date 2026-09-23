"""Starting a run on what arrived: the worker's half of ingress.

Split from ``test_ingress_service`` with `SurfaceTurnStarter`. The suite that
matters most here is the scope one -- `test_execute_chat_holds_no_session_during_io`
is the reason this object exists at all, and it reads as a plain assertion now
that there is only one mode to be in.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.tools.speech.provider import SpeechProviderError
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    AttachmentIngest,
    IngestedAttachment,
)
from app.modules.agent_surfaces.services.telegram_command_service import (
    handle_telegram_command,
)
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    TelegramMiniApp,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    transcribe_voice_attachments,
)
from app.modules.agent_surfaces.services.turn_starter import SurfaceTurnStarter
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    ScopeCountingFactory,
    _conversation,
    _registry,
    _slack_event,
    _slack_surface,
    _telegram_event,
    _telegram_surface,
    build_turn_starter,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


def _slack_chat_context(surface, conversation, text: str) -> SurfaceChatContext:
    return SurfaceChatContext(
        platform="SLACK",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text=text,
        message_metadata=SurfaceMessageMetadata(surface_platform="SLACK"),
        message_user_id=conversation.user_id,
        message_external_user_id="U123",
        event=_slack_event(),
    )


async def test_execute_chat_sends_direct_replies():
    parsed_event = _slack_event()
    adapter = AsyncMock()
    starter = build_turn_starter(adapter=adapter)
    starter._credentials_for = AsyncMock(
        return_value={"bot_token": "test-token", "access_token": "test-token"}
    )
    signup_context = SurfaceReplyContext(
        platform="SLACK",
        agent_display_name="Lemma",
        reply_message="Please sign up",
        event=parsed_event,
    )
    direct_context = SurfaceReplyContext(
        platform="SLACK",
        agent_display_name="Lemma",
        reply_message="Linked",
        reply_metadata={"reply_markup": {"remove_keyboard": True}},
        event=parsed_event,
    )

    await starter.execute_chat(signup_context)
    await starter.execute_chat(direct_context)

    assert adapter.send_message.await_count == 2
    assert adapter.send_message.await_args.kwargs["metadata"]["reply_markup"] == {
        "remove_keyboard": True
    }


async def test_execute_chat_names_the_surface_that_cannot_answer_a_stranger(caplog):
    """A surface with no credentials ignores every unrecognised sender.

    PS-SURF-012 says someone with no access is told how to get it rather than
    failing silently, and this branch is the one most likely to fire -- a
    surface whose account expired. The incident counter alone said "the fallback
    dependency is degraded" after three of them, without naming which surface,
    which is the one fact needed to fix it.
    """
    surface_id = uuid4()
    parsed_event = _telegram_event(chat_id="123", message_id="missing-creds")
    adapter = AsyncMock()
    starter = build_turn_starter(adapter=adapter)
    starter._credentials_for = AsyncMock(return_value={"bot_token": ""})
    context = SurfaceReplyContext(
        platform="TELEGRAM",
        surface_id=surface_id,
        reply_kind="signup",
        reply_message="Please sign up",
        event=parsed_event,
    )

    with caplog.at_level("WARNING"):
        await starter.execute_chat(context)

    adapter.send_message.assert_not_awaited()
    assert "surface_fallback_no_credentials" in caplog.text
    assert str(surface_id) in caplog.text


async def test_execute_chat_logs_delivery_failure_without_secret(monkeypatch):
    parsed_event = _telegram_event(chat_id="123", message_id="failed-delivery")
    adapter = AsyncMock()
    adapter.send_message.side_effect = RuntimeError("provider exposed secret-token")
    starter = build_turn_starter(adapter=adapter)
    starter._credentials_for = AsyncMock(return_value={"bot_token": "secret-token"})
    incident = Mock()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.fallback_reply_service._fallback_incident",
        incident,
    )
    context = SurfaceReplyContext(
        platform="TELEGRAM",
        reply_kind="surface_setup",
        reply_message="Set up a surface",
        event=parsed_event,
    )

    await starter.execute_chat(context)

    incident.record_failure.assert_called_once_with(error_type="RuntimeError")
    assert "secret-token" not in repr(incident.record_failure.call_args)


async def test_execute_chat_starts_agent_run_with_surface_metadata():
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    parsed_event = _slack_event()
    adapter = AsyncMock()
    starter = build_turn_starter(
        adapter=adapter, surfaces=[surface], conversation=conversation
    )
    context = SurfaceChatContext(
        platform="SLACK",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text="Hello from Slack",
        message_metadata=SurfaceMessageMetadata(
            surface_platform="SLACK",
            sender_display_name="New User",
            event_metadata={"attachments": [{"name": "notes.txt"}]},
        ),
        message_user_id=conversation.user_id,
        message_external_user_id="U123",
        message_external_message_id="1700000000.000100",
        event=parsed_event,
    )

    await starter.execute_chat(context)

    adapter.add_processing_indicator.assert_awaited_once()
    agent_conversations.start_surface_turn.assert_awaited_once()
    kwargs = agent_conversations.start_surface_turn.await_args.kwargs
    assert kwargs["conversation_id"] == conversation.id
    assert kwargs["pod_id"] == surface.pod_id
    assert kwargs["agent_name"] is None
    assert kwargs["message_metadata"]["surface_platform"] == "SLACK"
    assert kwargs["message_metadata"]["external_message_id"] == "1700000000.000100"


async def test_a_message_arriving_mid_run_is_not_acknowledged():
    """One message is routinely several webhooks on a chat surface, so an
    acknowledgement per bubble was the agent narrating its own plumbing. The
    run already going is told instead — see PendingUserMessagesCapability."""
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    adapter = AsyncMock()
    starter = build_turn_starter(
        adapter=adapter, surfaces=[surface], conversation=conversation
    )
    # ``None`` is how the operation says no new run was needed: the message was
    # handed to the one already going.
    agent_conversations.start_surface_turn.return_value = None

    for _ in range(3):
        await starter.execute_chat(_slack_chat_context(surface, conversation, "photo"))

    adapter.send_message.assert_not_awaited()


async def test_execute_chat_holds_no_session_during_io(monkeypatch):
    """No DB session is held during the external I/O.

    The processing indicator and the file ingest run with nothing open; the
    connection is taken only for the credential read and the message-write
    tail. This was "factory mode" of a two-mode object, and the mode is the
    object now -- there is no other way for `SurfaceTurnStarter` to run.
    """
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    parsed_event = _slack_event()
    adapter = AsyncMock()
    adapter.fetch_thread_context = AsyncMock(return_value=[])

    factory = ScopeCountingFactory()

    # Stub credential resolution + auth so the short UoWs do no real DB work.
    class _StubResolver:
        def __init__(self, *, uow) -> None:
            pass

        async def for_platform(self, platform, account_id, *, surface=None):
            return {}

    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.turn_starter.SurfaceCredentialResolver",
        _StubResolver,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_inbound_message.create_authorization_data_service",
        lambda uow: SimpleNamespace(
            build_user_context=AsyncMock(return_value=SimpleNamespace())
        ),
    )

    indicator_active: list[int] = []
    ingest_active: list[int] = []
    write_active: list[int] = []

    async def _record_indicator(**_kwargs):
        indicator_active.append(factory.active)

    async def _record_ingest(**_kwargs):
        ingest_active.append(factory.active)
        return AttachmentIngest()

    adapter.add_processing_indicator.side_effect = _record_indicator

    async def _record_write(*_args, **_kwargs):
        write_active.append(factory.active)

    agent_conversations.start_surface_turn.side_effect = _record_write

    starter = SurfaceTurnStarter(
        uow_factory=factory,
        adapter_registry=_registry(adapter),
        file_ingest_service=SimpleNamespace(ingest_attachments=_record_ingest),
    )

    context = SurfaceChatContext(
        platform="SLACK",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text="Hello from Slack",
        message_metadata=SurfaceMessageMetadata(
            surface_platform="SLACK",
            sender_display_name="New User",
            event_metadata={},
        ),
        message_user_id=conversation.user_id,
        message_external_user_id="U123",
        message_external_message_id="1700000000.000100",
        event=parsed_event,
    )

    await starter.execute_chat(context)

    # External I/O ran with NO open DB session.
    assert indicator_active == [0]
    assert ingest_active == [0]
    # The message write ran INSIDE a short UoW.
    assert write_active == [1]
    agent_conversations.start_surface_turn.assert_awaited_once()
    # Two short UoWs total: credential read + message-write tail.
    assert factory.opened == 2
    assert factory.active == 0


async def test_transcribe_voice_attachments_joins_caption_and_voice(monkeypatch):
    import app.modules.agent.tools.speech.provider as speech_provider

    class _Result:
        text = "schedule a meeting tomorrow"
        detected_language = "en"
        duration_seconds = 3.2

    class _Provider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            return _Result()

    monkeypatch.setattr(speech_provider, "get_speech_provider", lambda: _Provider())
    ingested = [
        IngestedAttachment(
            path="/me/telegram/note.ogg",
            name="note.ogg",
            mime="audio/ogg",
            content_type="voice",
            audio_bytes=b"OGG",
        )
    ]

    # Voice-only message → transcript becomes the whole prompt.
    meta: dict = {}
    text = await transcribe_voice_attachments(
        ingested=ingested, original_text="", metadata=meta
    )
    assert text == "schedule a meeting tomorrow"
    assert meta["voice_transcripts"][0]["path"] == "/me/telegram/note.ogg"
    assert meta["voice_transcripts"][0]["detected_language"] == "en"

    # Caption + voice → both, caption first.
    text2 = await transcribe_voice_attachments(
        ingested=ingested, original_text="fyi:", metadata={}
    )
    assert text2 == "fyi:\n\nschedule a meeting tomorrow"

    # The type word is not a caption. WhatsApp media carries none of its own,
    # so the parser falls back to the name of the kind of file it was -- and
    # every voice note reached the model as "audio\n\n<what they said>", which
    # reads as the person having typed the word "audio" first. Seven such
    # messages on dev, every one of them.
    text3 = await transcribe_voice_attachments(
        ingested=ingested, original_text="voice", metadata={}
    )
    assert text3 == "schedule a meeting tomorrow"

    # A word somebody really typed survives, even where it looks like one.
    text4 = await transcribe_voice_attachments(
        ingested=ingested, original_text="voice memo for you", metadata={}
    )
    assert text4 == "voice memo for you\n\nschedule a meeting tomorrow"


@pytest.mark.parametrize(
    "failure",
    [
        SpeechProviderError("deepgram down"),
        # A provider that breaks its own interface must not cost the person
        # their message either -- it is reported, not propagated.
        TimeoutError("the client raised something the interface never promised"),
    ],
    ids=["declared_failure", "undeclared_failure"],
)
async def test_transcribe_voice_falls_back_when_provider_fails(monkeypatch, failure):
    import app.modules.agent.tools.speech.provider as speech_provider

    class _Provider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            raise failure

    monkeypatch.setattr(speech_provider, "get_speech_provider", lambda: _Provider())
    ingested = [
        IngestedAttachment(
            path="/me/telegram/note.ogg",
            name="note.ogg",
            mime="audio/ogg",
            content_type="voice",
            audio_bytes=b"OGG",
        )
    ]
    meta: dict = {}
    text = await transcribe_voice_attachments(
        ingested=ingested, original_text="", metadata=meta
    )
    # Voice-only message never becomes an empty prompt.
    assert text == "[voice message]"
    assert meta["voice_transcription_failed"] is True


async def test_transcribe_noop_without_audio(monkeypatch):
    ingested = [
        IngestedAttachment(
            path="/me/slack/report.pdf", name="report.pdf", mime="application/pdf"
        )
    ]
    text = await transcribe_voice_attachments(
        ingested=ingested, original_text="see attached", metadata={}
    )
    assert text == "see attached"


async def test_a_whole_ingest_failure_still_tells_the_agent_the_files_arrived():
    """A blown-up ingest must not read to the agent as "they sent no files".

    Per-file failures are already reported through ``failed_files``. When
    ``ingest_attachments`` itself raises, the report was thrown away with the
    files: the agent answered the text alone and looked like it ignored the
    photo -- the exact outcome ``failed_files`` exists to prevent.
    """
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    starter = build_turn_starter(
        adapter=AsyncMock(), surfaces=[surface], conversation=conversation
    )
    starter.file_ingest_service = SimpleNamespace(
        ingest_attachments=AsyncMock(side_effect=RuntimeError("datastore is down"))
    )
    context = _slack_chat_context(surface, conversation, "here you go")
    context.event.metadata["attachments"] = [
        {"name": "receipt.pdf"},
        {"name": "photo.jpg"},
    ]

    await starter.execute_chat(context)

    kwargs = agent_conversations.start_surface_turn.await_args.kwargs
    failed = kwargs["message_metadata"]["failed_files"]
    assert [item["name"] for item in failed] == ["receipt.pdf", "photo.jpg"]


@pytest.mark.parametrize("command", ["/start", "/help"])
async def test_telegram_help_points_to_bound_mini_app_button(command, monkeypatch):
    surface = _telegram_surface()
    event = _telegram_event(chat_id="42", message_id="7")
    adapter = AsyncMock()
    starter = build_turn_starter(adapter=adapter, surfaces=[surface])
    app_id = uuid4()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_command_service."
        "_telegram_mini_app_for_context",
        AsyncMock(
            return_value=TelegramMiniApp(
                app_id=app_id,
                name="field-log",
                url="https://field-log.apps.example.test",
            )
        ),
    )
    context = SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=surface.pod_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Logger",
        message_text=command,
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=uuid4(),
        event=event,
    )

    handled = await handle_telegram_command(
        context=context,
        adapter=adapter,
        credentials={"bot_token": "secret"},
        uow_factory=starter.uow_factory,
    )

    assert handled is True
    sent = adapter.send_message.await_args.kwargs
    assert (
        "Open Field Log from the app button beside the message field" in sent["message"]
    )
    assert "metadata" not in sent


async def test_telegram_help_does_not_claim_unavailable_local_app_button(monkeypatch):
    surface = _telegram_surface()
    event = _telegram_event(chat_id="42", message_id="7")
    adapter = AsyncMock()
    starter = build_turn_starter(adapter=adapter, surfaces=[surface])
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_command_service."
        "_telegram_mini_app_for_context",
        AsyncMock(
            return_value=TelegramMiniApp(
                app_id=uuid4(),
                name="field-log",
                url=None,
            )
        ),
    )
    context = SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=surface.pod_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Logger",
        message_text="/help",
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=uuid4(),
        event=event,
    )

    handled = await handle_telegram_command(
        context=context,
        adapter=adapter,
        credentials={"bot_token": "secret"},
        uow_factory=starter.uow_factory,
    )

    assert handled is True
    sent = adapter.send_message.await_args.kwargs
    assert "app button beside the message field" not in sent["message"]


async def test_telegram_app_command_is_not_a_special_command():
    surface = _telegram_surface()
    event = _telegram_event(chat_id="42", message_id="7")
    adapter = AsyncMock()
    starter = build_turn_starter(adapter=adapter, surfaces=[surface])
    context = SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=surface.pod_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
        surface_id=surface.id,
        surface_config=surface.config,
        message_text="/app",
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=uuid4(),
        event=event,
    )

    handled = await handle_telegram_command(
        context=context,
        adapter=adapter,
        credentials={"bot_token": "secret"},
        uow_factory=starter.uow_factory,
    )

    assert handled is False
    adapter.send_message.assert_not_awaited()
