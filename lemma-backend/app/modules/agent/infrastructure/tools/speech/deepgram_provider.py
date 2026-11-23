"""Deepgram speech provider (STT + TTS) over the REST API via httpx.

Deepgram exposes single-POST endpoints, so we use httpx directly rather than
adding the heavier ``deepgram-sdk`` dependency.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import httpx

from app.core.config import reveal_secret
from app.modules.agent.config import agent_settings
from app.modules.agent.infrastructure.tools.speech.provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechProviderName,
    TranscriptionResult,
)

_LISTEN_URL = "https://api.deepgram.com/v1/listen"
_SPEAK_URL = "https://api.deepgram.com/v1/speak"
#: Flux voices (``flux-{voice}-{lang}``) are served here only.
_SPEAK_FLUX_URL = "https://api.deepgram.com/v2/speak"
#: The most text one speak request takes. Longer is refused with a 400 that
#: reads like a bad voice, so it is refused here, in words the agent can act on.
MAX_SPEAK_CHARS = 2000
_DEFAULT_STT_MODEL = "nova-3"
_DEFAULT_TTS_MODEL = "aura-2-thalia-en"
_STT_TIMEOUT = 120.0
_TTS_TIMEOUT = 60.0

# Deepgram speak `encoding`/container per requested output format, plus whether
# the format takes a `bit_rate`. Deepgram's defaults are 48000 for mp3 but only
# 12000 for opus — and opus is the format every native voice note uses, so the
# unset default is the one people hear.
_TTS_FORMAT_PARAMS: dict[str, dict[str, str]] = {
    "mp3": {"encoding": "mp3"},
    "wav": {"encoding": "linear16", "container": "wav"},
    "ogg": {"encoding": "opus", "container": "ogg"},
    "opus": {"encoding": "opus", "container": "ogg"},
}
# Bitrate ranges Deepgram accepts per encoding; linear16 takes none at all.
_TTS_BITRATE_RANGE: dict[str, tuple[int, int]] = {
    "mp3": (32000, 48000),
    "opus": (4000, 650000),
}


async def voice_for_language(language: str | None) -> str | None:
    """A voice that speaks this language, or None when the provider has none.

    A reply in Spanish read by an English voice is the same defect as a wrong
    transcript, so the language of the text picks the voice. This used to be a
    hand-written dict of one voice per language; it is the live catalogue now,
    which is both the whole range (roughly eighty voices, not seven) and the
    only source that cannot go stale. `voice_catalogue` keeps the old dict as
    its offline fallback.
    """
    from app.modules.agent.infrastructure.tools.speech.voice_catalogue import (
        default_voice_for,
        load_voices,
    )

    return default_voice_for(language, await load_voices())


class DeepgramSpeechProvider(SpeechProvider):
    name = SpeechProviderName.DEEPGRAM

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or reveal_secret(agent_settings.deepgram_api_key)

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Token {self._api_key}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def transcribe(
        self, audio_bytes: bytes, *, mime: str, language: str | None = None
    ) -> TranscriptionResult:
        if not self._api_key:
            raise SpeechProviderError("Deepgram API key is not configured.")
        params: dict[str, str] = {"model": _DEFAULT_STT_MODEL, "smart_format": "true"}
        params.update(_language_params(language))
        try:
            async with httpx.AsyncClient(timeout=_STT_TIMEOUT) as client:
                response = await client.post(
                    _LISTEN_URL,
                    params=params,
                    headers=self._headers(
                        content_type=mime or "application/octet-stream"
                    ),
                    content=audio_bytes,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
            return _parse_transcription(payload)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise SpeechProviderError(f"Deepgram transcription failed: {exc}") from exc

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        output_format: str = "mp3",
        language: str | None = None,
    ) -> bytes:
        if not self._api_key:
            raise SpeechProviderError("Deepgram API key is not configured.")
        model = await _speaking_model(text, voice=voice, language=language)
        params: dict[str, str] = {}
        format_params = _TTS_FORMAT_PARAMS.get(
            output_format.lower(), {"encoding": "mp3"}
        )
        params.update(format_params)
        bit_rate = _bitrate_for(format_params["encoding"])
        if bit_rate is not None:
            params["bit_rate"] = str(bit_rate)
        async with httpx.AsyncClient(timeout=_TTS_TIMEOUT) as client:
            try:
                return await self._speak(client, text, params, model)
            except httpx.HTTPStatusError as exc:
                # Deepgram retires and renames voices, and a name it no longer
                # knows is a 400 on the voice alone. Losing the accent is worth
                # far less than losing the reply, so fall back to the default
                # voice rather than failing the call.
                fallback = agent_settings.speech_tts_voice or _DEFAULT_TTS_MODEL
                # Not from a Flux voice: those speak what Aura cannot (Hindi),
                # and that text in the English default is not a reply.
                if (
                    exc.response.status_code != 400
                    or model == fallback
                    or model.startswith("flux-")
                ):
                    raise
                return await self._speak(client, text, params, fallback)

    async def _speak(
        self,
        client: httpx.AsyncClient,
        text: str,
        params: dict[str, str],
        model: str,
    ) -> bytes:
        response = await client.post(
            _SPEAK_FLUX_URL if model.startswith("flux-") else _SPEAK_URL,
            params={**params, "model": model},
            headers=self._headers(content_type="application/json"),
            json={"text": text},
        )
        response.raise_for_status()
        return response.content


async def _speaking_model(text: str, *, voice: str | None, language: str | None) -> str:
    """The voice to speak ``text`` in, or why it cannot be spoken: too long for
    one request, or in a language no voice speaks -- an English voice reading
    another script is noise, not a reply."""
    if len(text) > MAX_SPEAK_CHARS:
        raise SpeechProviderError(
            f"The text is {len(text)} characters; one spoken message takes "
            f"at most {MAX_SPEAK_CHARS}. Say the gist and send the rest as text."
        )
    if voice:
        return voice
    language_voice = await voice_for_language(language)
    if language and not _is_english(language) and not language_voice:
        raise SpeechProviderError(
            f"No voice speaks '{language}' yet; reply in text instead."
        )
    return language_voice or agent_settings.speech_tts_voice or _DEFAULT_TTS_MODEL


def _is_english(language: str) -> bool:
    return language.strip().lower().split("-", 1)[0] == "en"


def _language_params(language: str | None) -> dict[str, str]:
    """Deepgram's language knobs for a requested language.

    An explicit code wins. Otherwise the configured default decides between
    Nova-3 multilingual code-switching (``multi``, the default — one voice note
    that mixes two languages transcribes as both) and whole-file detection
    (``auto``, which reaches more languages but picks exactly one).
    """
    requested = (
        str(language or agent_settings.speech_stt_language or "multi").strip().lower()
    )
    if requested in {"auto", "detect"}:
        return {"detect_language": "true"}
    return {"language": requested or "multi"}


def _bitrate_for(encoding: str) -> int | None:
    """The configured bitrate, clamped to what Deepgram accepts for ``encoding``."""
    bounds = _TTS_BITRATE_RANGE.get(encoding)
    if bounds is None:
        return None
    low, high = bounds
    configured = int(agent_settings.speech_tts_bitrate or high)
    return max(low, min(high, configured))


def _detected_language(
    channel: dict[str, Any], alternative: dict[str, Any]
) -> str | None:
    """The language Deepgram heard, however this response reports it.

    Whole-file detection puts one code on the channel. Multilingual
    code-switching tags each word instead, so the file's language is the one
    most of its words were in — which is what picks the reply voice.
    """
    detected = channel.get("detected_language")
    if detected:
        return str(detected)
    words = alternative.get("words")
    if not isinstance(words, list):
        return None
    tags = Counter(
        str(word.get("language"))
        for word in words
        if isinstance(word, dict) and word.get("language")
    )
    if not tags:
        return None
    return tags.most_common(1)[0][0]


def _parse_transcription(payload: dict[str, Any]) -> TranscriptionResult:
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    first = channels[0] if channels else {}
    alternatives = (first or {}).get("alternatives") or []
    alternative = alternatives[0] if alternatives else {}
    transcript = str(alternative.get("transcript") or "")
    metadata = payload.get("metadata") or {}
    duration = metadata.get("duration")
    return TranscriptionResult(
        text=transcript,
        detected_language=_detected_language(first or {}, alternative),
        duration_seconds=float(duration)
        if isinstance(duration, (int, float))
        else None,
    )
