"""One vocabulary for every voice engine.

A call does not know whether Gemini, OpenAI or a Deepgram cascade is talking.
Each engine turns its provider's frames into these events and takes the same
three inputs -- audio, text to know, text to say -- so the session above it,
the floor it keeps and the agents behind it are written once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

#: What the caller's microphone sends: 16-bit little-endian mono PCM.
INPUT_SAMPLE_RATE = 16_000


class Inject(StrEnum):
    """How text reaches the voice."""

    #: Known from now on, said by nobody. The voice uses it when it is asked.
    CONTEXT = "context"
    #: Said now, in the voice's own words.
    SPEAK = "speak"


@dataclass(frozen=True, slots=True)
class AudioOut:
    pcm: bytes
    sample_rate: int


@dataclass(frozen=True, slots=True)
class UserSpeaking:
    """The caller started talking. Over the voice, this is a barge-in."""


@dataclass(frozen=True, slots=True)
class Heard:
    """What the caller said; ``final`` closes their turn."""

    text: str
    final: bool


@dataclass(frozen=True, slots=True)
class Said:
    """What the voice said, as transcribed; ``final`` closes its turn."""

    text: str
    final: bool


@dataclass(frozen=True, slots=True)
class Interrupted:
    """The voice stopped mid-sentence because the caller cut in. Whatever audio
    the caller has not heard yet must be dropped, not played late."""


@dataclass(frozen=True, slots=True)
class TurnDone:
    """The voice finished its turn."""


@dataclass(frozen=True, slots=True)
class EngineClosed:
    """The provider is gone. ``error`` is a sentence for the caller, or None for
    an ordinary end."""

    error: str | None = None


type VoiceEvent = (
    AudioOut | UserSpeaking | Heard | Said | Interrupted | TurnDone | EngineClosed
)


class VoiceEngine(Protocol):
    """A live voice: audio in, audio and transcripts out."""

    #: The sample rate of the audio this engine emits.
    output_sample_rate: int

    async def open(self) -> None:
        """Connect to the provider; raises ``EngineUnavailable`` on failure."""
        ...

    async def push_audio(self, pcm: bytes) -> None: ...

    async def inject(self, text: str, mode: Inject) -> None: ...

    def events(self) -> AsyncIterator[VoiceEvent]: ...

    async def close(self) -> None: ...


class EngineUnavailable(Exception):
    """An engine could not start: a missing key, a refused connection. The
    message is safe to show the caller."""
