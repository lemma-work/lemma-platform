"""Turning voice notes into text, for surfaces that receive them.

Replaces the `get_speech_provider` factory in `app/composition/surface_agent.py`.
The factory made `agent_surfaces` hold the provider: it resolved one, guarded
the resolution in a `try/except Exception`, guarded each transcription in a
second one, and read the result with `getattr(result, "text", "")` because what
came back was typed `Any`. Three defences against a provider it had no business
holding.

Transcription takes the whole batch rather than one clip, because how many
providers to resolve and whether to run the clips concurrently are questions
about transcription, not about an inbound message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

from app.core.log.log import get_logger
from app.modules.agent.tools.speech.provider import SpeechProviderError

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VoiceClip:
    audio_bytes: bytes
    mime: str


@dataclass(frozen=True, slots=True)
class VoiceTranscript:
    text: str
    detected_language: str | None = None
    duration_seconds: float | None = None


async def transcribe_voice_notes(
    clips: Sequence[VoiceClip],
) -> list[VoiceTranscript | None]:
    """One transcript per clip, in order, with ``None`` where it did not work.

    ``None`` rather than a raise throughout: a voice note that would not
    transcribe still arrived, and the caller delivers the message without it.
    """
    if not clips:
        return []

    from app.modules.agent.tools.speech.provider import get_speech_provider

    try:
        provider = get_speech_provider()
    except (SpeechProviderError, ValueError, ImportError) as exc:
        # Voice notes arrive untranscribed from here on, which is a user-visible
        # degradation -- so it stays a warning, but it has to name the failure.
        logger.warning(
            "agent.speech.provider_unavailable",
            error_type=type(exc).__name__,
        )
        return [None] * len(clips)

    return list(await asyncio.gather(*[_transcribe(provider, clip) for clip in clips]))


async def _transcribe(provider, clip: VoiceClip) -> VoiceTranscript | None:
    try:
        result = await provider.transcribe(clip.audio_bytes, mime=clip.mime)
    except SpeechProviderError:
        return None
    except Exception as exc:
        # A provider raising outside its declared failure mode is a bug in that
        # provider, and it is still not a reason to lose the message this voice
        # note arrived with -- so it is reported loudly and the clip comes back
        # untranscribed like any other failure.
        logger.error(
            "agent.speech.provider_broke_its_contract",
            error_type=type(exc).__name__,
            message=str(exc),
            exc_info=True,
        )
        return None
    return VoiceTranscript(
        text=result.text,
        detected_language=result.detected_language,
        duration_seconds=result.duration_seconds,
    )


__all__ = ["VoiceClip", "VoiceTranscript", "transcribe_voice_notes"]
