from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

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

    async def synthesize(self, text, *, voice=None, output_format="mp3"):
        self.synthesize_args = {"text": text, "voice": voice, "format": output_format}
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
