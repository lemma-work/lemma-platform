"""OpenAI's realtime model, over its server-side WebSocket.

The voice only talks, so the default is the mini model: the agents behind the
call do the thinking, and the full model would bill audio at several times the
rate for reasoning nobody asked it for.

The protocol takes 24 kHz PCM, so the caller's 16 kHz audio is converted on the
way in. Over a WebSocket the provider cannot know how much of its reply the
caller heard before cutting in; the engine truncates the reply at what it had
delivered, which is the closest this side can know.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from pydantic import JsonValue

from app.modules.calls.domain.frames import (
    EVENT_BACKLOG,
    MAX_PROVIDER_FRAME_BYTES,
    decode,
    encode,
    offer,
)
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.calls.domain.pcm import duration_ms, resample
from app.modules.calls.domain.voice import (
    INPUT_SAMPLE_RATE,
    AudioOut,
    EngineClosed,
    EngineUnavailable,
    Heard,
    Inject,
    Interrupted,
    Said,
    TurnDone,
    UserSpeaking,
    VoiceEvent,
)

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

logger = get_logger(__name__)

_ENDPOINT = "wss://api.openai.com/v1/realtime"
PROVIDER_SAMPLE_RATE = 24_000
_OPEN_TIMEOUT_SECONDS = 15.0
_INPUT_TRANSCRIBER = "gpt-4o-mini-transcribe"

type Frame = dict[str, JsonValue]


class OpenAIRealtimeEngine:
    output_sample_rate = PROVIDER_SAMPLE_RATE

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        instructions: str,
        voice: str,
        endpoint: str = _ENDPOINT,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._instructions = instructions
        self._voice = voice
        self._endpoint = endpoint
        self._socket: ClientConnection | None = None
        self._events: asyncio.Queue[VoiceEvent] = asyncio.Queue(maxsize=EVENT_BACKLOG)
        self._reader: asyncio.Task[None] | None = None
        self._item_id: str | None = None
        self._delivered_ms = 0.0
        #: When the reply's first audio went out. The caller hears it in real
        #: time from then, which is what a truncate has to measure.
        self._audio_started_at: float | None = None
        self._configured = False
        #: A response is being generated; the provider refuses another until
        #: it is done.
        self._responding = False
        #: An update asked to be spoken while one was: start it after.
        self._speak_owed = False

    async def open(self) -> None:
        import websockets

        try:
            self._socket = await websockets.connect(
                f"{self._endpoint}?model={self._model}",
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                max_size=MAX_PROVIDER_FRAME_BYTES,
                open_timeout=_OPEN_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError, websockets.WebSocketException) as exc:
            logger.warning("calls.openai.connect.failed", exc_info=True)
            raise EngineUnavailable("The voice could not connect.") from exc
        audio_format: Frame = {"type": "audio/pcm", "rate": PROVIDER_SAMPLE_RATE}
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": self._instructions,
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": audio_format,
                            "transcription": {"model": _INPUT_TRANSCRIBER},
                            "turn_detection": {"type": "semantic_vad"},
                        },
                        "output": {"format": audio_format, "voice": self._voice},
                    },
                },
            }
        )
        self._reader = create_inherited_task(self._read(), name="calls.openai.read")

    async def _send(self, frame: Frame) -> None:
        if self._socket is None:
            return
        import websockets

        with contextlib.suppress(websockets.ConnectionClosed):
            await self._socket.send(encode(frame))

    async def push_audio(self, pcm: bytes) -> None:
        converted = resample(pcm, INPUT_SAMPLE_RATE, PROVIDER_SAMPLE_RATE)
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(converted).decode("ascii"),
            }
        )

    async def inject(self, text: str, mode: Inject) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        if mode is not Inject.SPEAK:
            return
        if self._responding:
            # Asked again mid-response the provider answers with an error and
            # the update is never said: it is started once this one is done.
            self._speak_owed = True
            return
        await self._send({"type": "response.create"})

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, EngineClosed):
                return

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._socket is not None:
            await self._socket.close()

    async def _read(self) -> None:
        import websockets

        socket = self._socket
        if socket is None:
            return
        try:
            async for raw in socket:
                closed = await self._handle(decode(raw))
                if closed is not None:
                    offer(self._events, closed, essential=True)
                    return
        except websockets.ConnectionClosed:
            offer(
                self._events,
                EngineClosed("The voice connection dropped. Please call again."),
                essential=True,
            )
            return
        offer(self._events, EngineClosed(), essential=True)

    async def _handle(self, frame: dict[str, JsonValue]) -> EngineClosed | None:
        match frame.get("type"):
            case "input_audio_buffer.speech_started":
                offer(self._events, UserSpeaking())
                # Audio arrives faster than it plays, so a reply the provider
                # has finished can still be playing; that one is cut too.
                if self._still_playing():
                    await self._truncate()
            case "session.updated":
                self._configured = True
            case "conversation.item.input_audio_transcription.delta":
                offer(self._events, Heard(_str(frame.get("delta")), final=False))
            case "conversation.item.input_audio_transcription.completed":
                offer(self._events, Heard(_str(frame.get("transcript")), final=True))
            case "response.created":
                self._responding = True
                self._delivered_ms = 0.0
                self._audio_started_at = None
            case "response.output_audio.delta":
                self._item_id = _str(frame.get("item_id")) or self._item_id
                pcm = base64.b64decode(_str(frame.get("delta")))
                if self._audio_started_at is None:
                    self._audio_started_at = time.monotonic()
                self._delivered_ms += duration_ms(pcm, PROVIDER_SAMPLE_RATE)
                offer(self._events, AudioOut(pcm, PROVIDER_SAMPLE_RATE))
            case "response.output_audio_transcript.delta":
                offer(self._events, Said(_str(frame.get("delta")), final=False))
            case "response.output_audio_transcript.done":
                offer(self._events, Said(_str(frame.get("transcript")), final=True))
            case "response.done":
                self._responding = False
                offer(self._events, TurnDone())
                if self._speak_owed:
                    self._speak_owed = False
                    await self._send({"type": "response.create"})
            case "error":
                return self._error(frame)
        return None

    def _played_ms(self) -> float:
        if self._audio_started_at is None:
            return 0.0
        elapsed = (time.monotonic() - self._audio_started_at) * 1000
        return min(self._delivered_ms, elapsed)

    def _still_playing(self) -> bool:
        return (
            self._audio_started_at is not None
            and self._played_ms() < self._delivered_ms
        )

    async def _truncate(self) -> None:
        offer(self._events, Interrupted())
        played = self._played_ms()
        self._audio_started_at = None
        if self._item_id is None:
            return
        await self._send(
            {
                "type": "conversation.item.truncate",
                "item_id": self._item_id,
                "content_index": 0,
                "audio_end_ms": int(played),
            }
        )

    def _error(self, frame: dict[str, JsonValue]) -> EngineClosed | None:
        error = frame.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        # A truncate that lost a race with the reply ending, or a cancel with
        # nothing to cancel, is the provider being precise, not a failure.
        if code in {"response_cancel_not_active", "item_truncate_invalid_item_id"}:
            return None
        logger.warning("calls.openai.provider_error.degraded", code=str(code))
        if code in {"invalid_api_key", "session_expired", "insufficient_quota"}:
            return EngineClosed("The voice provider ended the call.")
        if not self._configured:
            # The session itself was refused: a voice that never got its
            # instructions or audio format cannot hold a call.
            return EngineClosed("The voice could not be set up.")
        return None


def _str(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""
