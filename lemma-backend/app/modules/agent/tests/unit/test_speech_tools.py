from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx

import app.modules.agent.tools.speech.speech as speech_module
from app.modules.agent.tools.speech.models import (
    ListenRequest,
    SayRequest,
)
from app.modules.agent.tools.speech.provider import (
    SpeechProviderName,
    TranscriptionResult,
    get_speech_provider,
)
from app.modules.agent.tools.speech.deepgram_provider import DeepgramSpeechProvider


class _StubProvider:
    def __init__(self, *, transcript="hello world", audio=b"AUDIOBYTES"):
        self._transcript = transcript
        self._audio = audio
        self.transcribe_args: dict = {}
        self.synthesize_args: dict = {}

    async def transcribe(self, audio_bytes, *, mime, language=None):
        self.transcribe_args = {
            "bytes": audio_bytes,
            "mime": mime,
            "language": language,
        }
        return TranscriptionResult(
            text=self._transcript, detected_language="en", duration_seconds=2.0
        )

    async def synthesize(self, text, *, voice=None, output_format="mp3", language=None):
        self.synthesize_args = {
            "text": text,
            "voice": voice,
            "format": output_format,
            "language": language,
        }
        return self._audio


class _FakeFileService:
    def __init__(self):
        self.created: dict = {}

    async def create_file(
        self,
        *,
        pod_id,
        name,
        file_content,
        ctx,
        directory_path,
        search_enabled=True,
        **kwargs,
    ):
        self.created = {
            "pod_id": pod_id,
            "name": name,
            "size": len(file_content),
            "directory_path": directory_path,
        }
        return SimpleNamespace(path=f"{directory_path}/{name}")


def _fake_pod_services(file_service):
    class _Ctx:
        def __init__(self, deps):
            self.file = file_service
            self.ctx = SimpleNamespace()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    return _Ctx


def _listen_deps(content):
    async def _read_file(path):
        return content

    return SimpleNamespace(
        file_manager=SimpleNamespace(read_file=_read_file),
        pod_id=uuid4(),
        user_id=uuid4(),
    )


# ---- factory ---------------------------------------------------------------


def test_factory_explicit_and_auto_return_deepgram():
    assert isinstance(
        get_speech_provider(SpeechProviderName.DEEPGRAM), DeepgramSpeechProvider
    )
    assert isinstance(get_speech_provider(), DeepgramSpeechProvider)


# ---- listen ----------------------------------------------------------------


async def test_listen_transcribes_sandbox_file(monkeypatch):
    stub = _StubProvider(transcript="the user said hi")
    monkeypatch.setattr(speech_module, "get_speech_provider", lambda *a, **k: stub)

    result = await speech_module.listen_internal(
        _listen_deps(b"OGGDATA"), ListenRequest(file_path="voice.ogg")
    )

    assert result.success is True
    assert result.transcript == "the user said hi"
    assert result.detected_language == "en"
    assert stub.transcribe_args["bytes"] == b"OGGDATA"
    # mime derived from the .ogg extension
    assert "ogg" in (stub.transcribe_args["mime"] or "")


async def test_listen_coerces_str_content_to_bytes(monkeypatch):
    stub = _StubProvider()
    monkeypatch.setattr(speech_module, "get_speech_provider", lambda *a, **k: stub)

    await speech_module.listen_internal(
        _listen_deps("plain text decoded"), ListenRequest(file_path="note.wav")
    )
    assert stub.transcribe_args["bytes"] == b"plain text decoded"


async def test_listen_file_not_found(monkeypatch):
    async def _raise(_path):
        raise FileNotFoundError("nope")

    deps = SimpleNamespace(
        file_manager=SimpleNamespace(read_file=_raise),
        pod_id=uuid4(),
        user_id=uuid4(),
    )
    result = await speech_module.listen_internal(
        deps, ListenRequest(file_path="missing.mp3")
    )
    assert result.success is False
    assert "not found" in (result.error or "").lower()


async def test_listen_requires_path():
    result = await speech_module.listen_internal(
        SimpleNamespace(), ListenRequest(file_path="")
    )
    assert result.success is False


# ---- say -------------------------------------------------------------------


async def test_say_writes_mp3_to_datastore(monkeypatch):
    stub = _StubProvider(audio=b"MP3BYTES")
    file_service = _FakeFileService()
    monkeypatch.setattr(speech_module, "get_speech_provider", lambda *a, **k: stub)
    monkeypatch.setattr(speech_module, "pod_services", _fake_pod_services(file_service))

    deps = SimpleNamespace(pod_id=uuid4())
    result = await speech_module.say_internal(deps, SayRequest(text="Hello there"))

    assert result.success is True
    assert result.audio_file_path.startswith("/me/speech/")
    assert result.audio_file_path.endswith(".mp3")
    assert file_service.created["directory_path"] == "/me/speech"
    assert file_service.created["size"] == len(b"MP3BYTES")
    assert stub.synthesize_args["text"] == "Hello there"
    assert stub.synthesize_args["format"] == "mp3"


async def test_say_honors_explicit_output_path(monkeypatch):
    stub = _StubProvider()
    file_service = _FakeFileService()
    monkeypatch.setattr(speech_module, "get_speech_provider", lambda *a, **k: stub)
    monkeypatch.setattr(speech_module, "pod_services", _fake_pod_services(file_service))

    deps = SimpleNamespace(pod_id=uuid4())
    result = await speech_module.say_internal(
        deps, SayRequest(text="hi", output_file_path="/me/replies/answer.mp3")
    )
    assert result.audio_file_path == "/me/replies/answer.mp3"
    assert file_service.created["directory_path"] == "/me/replies"
    assert file_service.created["name"] == "answer.mp3"


async def test_say_requires_text():
    result = await speech_module.say_internal(
        SimpleNamespace(pod_id=uuid4()), SayRequest(text="   ")
    )
    assert result.success is False


# ---- language + audio quality ----------------------------------------------


def test_stt_defaults_to_multilingual_not_single_language(monkeypatch):
    from app.modules.agent.config import agent_settings
    from app.modules.agent.tools.speech.deepgram_provider import _language_params

    monkeypatch.setattr(agent_settings, "speech_stt_language", "multi")
    # A voice note that switches languages mid-sentence must not be forced into
    # one of them, which is what whole-file detection does.
    assert _language_params(None) == {"language": "multi"}
    assert _language_params("hi") == {"language": "hi"}


def test_stt_auto_setting_uses_whole_file_detection(monkeypatch):
    from app.modules.agent.config import agent_settings
    from app.modules.agent.tools.speech.deepgram_provider import _language_params

    monkeypatch.setattr(agent_settings, "speech_stt_language", "auto")
    assert _language_params(None) == {"detect_language": "true"}


def test_detected_language_falls_back_to_word_tags():
    from app.modules.agent.tools.speech.deepgram_provider import _parse_transcription

    # Multilingual code-switching tags words, not the channel.
    result = _parse_transcription(
        {
            "results": {
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "hola there",
                                "words": [
                                    {"word": "hola", "language": "es"},
                                    {"word": "there", "language": "es"},
                                    {"word": "amigo", "language": "en"},
                                ],
                            }
                        ]
                    }
                ]
            },
            "metadata": {"duration": 1.5},
        }
    )
    assert result.text == "hola there"
    assert result.detected_language == "es"
    assert result.duration_seconds == 1.5


def test_detected_language_prefers_channel_when_present():
    from app.modules.agent.tools.speech.deepgram_provider import _parse_transcription

    result = _parse_transcription(
        {
            "results": {
                "channels": [
                    {
                        "detected_language": "ja",
                        "alternatives": [{"transcript": "こんにちは"}],
                    }
                ]
            },
            "metadata": {},
        }
    )
    assert result.detected_language == "ja"


def test_opus_bitrate_is_raised_off_deepgrams_12k_default(monkeypatch):
    from app.modules.agent.config import agent_settings
    from app.modules.agent.tools.speech.deepgram_provider import _bitrate_for

    monkeypatch.setattr(agent_settings, "speech_tts_bitrate", 48000)
    # Opus is what every native voice note is encoded as; Deepgram's own
    # default for it is 12000.
    assert _bitrate_for("opus") == 48000
    # mp3 tops out at 48000, so a higher setting clamps rather than 400s.
    monkeypatch.setattr(agent_settings, "speech_tts_bitrate", 128000)
    assert _bitrate_for("mp3") == 48000
    assert _bitrate_for("opus") == 128000
    # linear16 takes no bitrate at all.
    assert _bitrate_for("linear16") is None


def test_voice_follows_the_language_being_spoken():
    from app.modules.agent.tools.speech.deepgram_provider import voice_for_language

    assert voice_for_language("es") == "aura-2-celeste-es"
    assert voice_for_language("ja-JP") == "aura-2-uzume-ja"
    assert voice_for_language("en-US") == "aura-2-thalia-en"
    # Deepgram has no Hindi voice — the caller falls back to the default.
    assert voice_for_language("hi") is None
    assert voice_for_language(None) is None


async def test_say_passes_language_to_the_provider(monkeypatch):
    stub = _StubProvider()
    file_service = _FakeFileService()
    monkeypatch.setattr(speech_module, "get_speech_provider", lambda *a, **k: stub)
    monkeypatch.setattr(speech_module, "pod_services", _fake_pod_services(file_service))

    await speech_module.say_internal(
        SimpleNamespace(pod_id=uuid4()), SayRequest(text="Hola", language="es")
    )
    assert stub.synthesize_args["language"] == "es"


# ---- what actually goes on the wire ----------------------------------------


class _RecordingTransport(httpx.AsyncBaseTransport):
    """An httpx transport that records requests and replays scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self._responses.pop(0)
        return httpx.Response(status, content=body, request=request)


def _patch_transport(monkeypatch, responses):
    transport = _RecordingTransport(responses)
    original = httpx.AsyncClient.__init__

    def _init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return transport


async def test_synthesize_sends_a_bitrate_for_opus(monkeypatch):
    from app.modules.agent.config import agent_settings

    monkeypatch.setattr(agent_settings, "speech_tts_bitrate", 48000)
    transport = _patch_transport(monkeypatch, [(200, b"AUDIO")])

    audio = await DeepgramSpeechProvider(api_key="k").synthesize(
        "hola", output_format="ogg", language="es"
    )

    assert audio == b"AUDIO"
    params = transport.requests[0].url.params
    # Unset, Deepgram encodes Opus at 12000 — and Opus is what WhatsApp and
    # Telegram voice notes are.
    assert params["bit_rate"] == "48000"
    assert params["encoding"] == "opus"
    assert params["container"] == "ogg"
    assert params["model"] == "aura-2-celeste-es"


async def test_synthesize_falls_back_when_the_voice_is_rejected(monkeypatch):
    from app.modules.agent.config import agent_settings

    monkeypatch.setattr(agent_settings, "speech_tts_voice", "aura-2-thalia-en")
    transport = _patch_transport(
        monkeypatch, [(400, b"unknown model"), (200, b"AUDIO")]
    )

    audio = await DeepgramSpeechProvider(api_key="k").synthesize(
        "ciao", output_format="mp3", language="it"
    )

    assert audio == b"AUDIO"
    assert transport.requests[0].url.params["model"] == "aura-2-melia-it"
    # The reply still goes out, in the default voice.
    assert transport.requests[1].url.params["model"] == "aura-2-thalia-en"


async def test_transcribe_asks_for_multilingual_by_default(monkeypatch):
    from app.modules.agent.config import agent_settings

    monkeypatch.setattr(agent_settings, "speech_stt_language", "multi")
    transport = _patch_transport(
        monkeypatch,
        [
            (
                200,
                b'{"results": {"channels": [{"alternatives": '
                b'[{"transcript": "kal meeting hai"}]}]}, "metadata": {}}',
            )
        ],
    )

    result = await DeepgramSpeechProvider(api_key="k").transcribe(
        b"OGG", mime="audio/ogg"
    )

    assert result.text == "kal meeting hai"
    params = transport.requests[0].url.params
    assert params["model"] == "nova-3"
    assert params["language"] == "multi"
    assert "detect_language" not in params
