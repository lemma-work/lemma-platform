"""Deepgram speech provider (STT + TTS) over the REST API via httpx.

Deepgram exposes single-POST endpoints, so we use httpx directly rather than
adding the heavier ``deepgram-sdk`` dependency.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.modules.agent.config import agent_settings
from app.modules.agent.tools.speech.provider import (
    SpeechProvider,
    SpeechProviderName,
    TranscriptionResult,
)

_LISTEN_URL = "https://api.deepgram.com/v1/listen"
_SPEAK_URL = "https://api.deepgram.com/v1/speak"
_DEFAULT_STT_MODEL = "nova-3"
_DEFAULT_TTS_MODEL = "aura-2-thalia-en"
_STT_TIMEOUT = 120.0
_TTS_TIMEOUT = 60.0

# Deepgram speak `encoding`/container per requested output format.
_TTS_FORMAT_PARAMS: dict[str, dict[str, str]] = {
    "mp3": {"encoding": "mp3"},
    "wav": {"encoding": "linear16", "container": "wav"},
    "ogg": {"encoding": "opus", "container": "ogg"},
    "opus": {"encoding": "opus", "container": "ogg"},
}


class DeepgramSpeechProvider(SpeechProvider):
    name = SpeechProviderName.DEEPGRAM

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or agent_settings.deepgram_api_key

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
            raise RuntimeError("Deepgram API key is not configured.")
        params: dict[str, str] = {"model": _DEFAULT_STT_MODEL, "smart_format": "true"}
        if language:
            params["language"] = language
        else:
            params["detect_language"] = "true"
        async with httpx.AsyncClient(timeout=_STT_TIMEOUT) as client:
            response = await client.post(
                _LISTEN_URL,
                params=params,
                headers=self._headers(content_type=mime or "application/octet-stream"),
                content=audio_bytes,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        return _parse_transcription(payload)

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        output_format: str = "mp3",
    ) -> bytes:
        if not self._api_key:
            raise RuntimeError("Deepgram API key is not configured.")
        params: dict[str, str] = {"model": voice or _DEFAULT_TTS_MODEL}
        params.update(
            _TTS_FORMAT_PARAMS.get(output_format.lower(), {"encoding": "mp3"})
        )
        async with httpx.AsyncClient(timeout=_TTS_TIMEOUT) as client:
            response = await client.post(
                _SPEAK_URL,
                params=params,
                headers=self._headers(content_type="application/json"),
                json={"text": text},
            )
            response.raise_for_status()
            return response.content


def _parse_transcription(payload: dict[str, Any]) -> TranscriptionResult:
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    first = channels[0] if channels else {}
    alternatives = (first or {}).get("alternatives") or []
    transcript = str((alternatives[0] if alternatives else {}).get("transcript") or "")
    detected_language = (first or {}).get("detected_language")
    metadata = payload.get("metadata") or {}
    duration = metadata.get("duration")
    return TranscriptionResult(
        text=transcript,
        detected_language=detected_language if detected_language else None,
        duration_seconds=float(duration)
        if isinstance(duration, (int, float))
        else None,
    )
