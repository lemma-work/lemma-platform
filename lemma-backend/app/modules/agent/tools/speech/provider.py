"""Pluggable speech (STT/TTS) provider interface.

Adding a provider = implement :class:`SpeechProvider` in a new module, add one
entry to ``_PROVIDERS`` and a ``SpeechProviderName`` member (and optionally
extend ``agent_settings.speech_provider``). The tools and orchestration in
``speech.py`` never change. Mirrors the settings-driven dict-factory pattern in
``app/core/web_search/search_client.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from app.modules.agent.config import agent_settings


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    detected_language: str | None = None
    duration_seconds: float | None = None


class SpeechProviderName(str, Enum):
    DEEPGRAM = "deepgram"
    # Future: ELEVENLABS = "elevenlabs", WHISPER = "whisper", GOOGLE = "google"


class SpeechProvider(ABC):
    name: SpeechProviderName

    def is_available(self) -> bool:
        """Whether the provider has the credentials/config it needs."""
        return True

    @abstractmethod
    async def transcribe(
        self, audio_bytes: bytes, *, mime: str, language: str | None = None
    ) -> TranscriptionResult: ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        output_format: str = "mp3",
        language: str | None = None,
    ) -> bytes:
        """Speak ``text``.

        ``language`` is the language the text is in, so a provider can pick a
        voice that speaks it; an explicit ``voice`` overrides that.
        """


def _build(name: SpeechProviderName) -> SpeechProvider:
    # Imported lazily to keep provider modules optional.
    from app.modules.agent.tools.speech.deepgram_provider import (
        DeepgramSpeechProvider,
    )

    providers: dict[SpeechProviderName, type[SpeechProvider]] = {
        SpeechProviderName.DEEPGRAM: DeepgramSpeechProvider,
    }
    return providers[name]()


def get_speech_provider(name: SpeechProviderName | None = None) -> SpeechProvider:
    """Resolve a speech provider from an explicit name or ``agent_settings.speech_provider``.

    ``auto`` selects the first available provider (falling back to the default
    even if unavailable, so the caller surfaces a clear credential error).
    """
    if name is not None:
        return _build(name)

    configured = str(agent_settings.speech_provider or "auto").strip().lower()
    if configured != "auto":
        return _build(SpeechProviderName(configured))

    for candidate in (SpeechProviderName.DEEPGRAM,):
        provider = _build(candidate)
        if provider.is_available():
            return provider
    return _build(SpeechProviderName.DEEPGRAM)
