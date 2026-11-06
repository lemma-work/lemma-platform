"""Gemini Live, over its bidirectional WebSocket.

The wire protocol rather than the SDK: four message shapes in each direction,
and the SDK's session object ends its receive loop at every turn boundary,
which is the wrong shape for a call that lasts an hour.

Two channels carry text in, and which one decides whether the voice talks:

- ``clientContent`` with the turn left open is context. The model knows it and
  says nothing until it is relevant. Sent as live input instead, a bookkeeping
  note reads as the caller speaking, and the model answers it out loud.
- ``realtimeInput.text`` is spoken to: the model responds to it at once, in its
  own words, which is what an update the agents want said needs.

The same protocol is served by the Gemini API, with a key, and by Vertex AI,
with the workload's service account; ``google_auth`` builds either.

A connection lasts about ten minutes. The server warns with ``goAway`` and keeps
handing out resumption handles; the engine reconnects with the latest one and
the conversation carries on where it was.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
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

    from app.modules.calls.infrastructure.engines.google_auth import Headers

logger = get_logger(__name__)

_ENDPOINT = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
OUTPUT_SAMPLE_RATE = 24_000
#: How long the caller's transcript must be quiet, once the voice has begun,
#: before their turn is taken as finished.
CALLER_TURN_GRACE_SECONDS = 0.6
_SETUP_TIMEOUT_SECONDS = 15.0
#: Each reconnect replays nothing; more than a few in a row means the provider
#: is refusing us, not rotating the connection.
_MAX_RECONNECTS = 3
#: Text frames kept across one reconnect, the newest.
_MAX_HELD_TEXT = 20

type Frame = dict[str, JsonValue]


class GeminiLiveEngine:
    output_sample_rate = OUTPUT_SAMPLE_RATE

    def __init__(
        self,
        *,
        headers: Headers,
        model: str,
        instructions: str,
        voice: str | None = None,
        endpoint: str = _ENDPOINT,
    ) -> None:
        self._headers = headers
        self._model = (
            model if model.startswith(("models/", "projects/")) else f"models/{model}"
        )
        self._instructions = instructions
        self._voice = voice
        self._endpoint = endpoint
        self._socket: ClientConnection | None = None
        self._events: asyncio.Queue[VoiceEvent] = asyncio.Queue(maxsize=EVENT_BACKLOG)
        self._reader: asyncio.Task[None] | None = None
        self._handle: str | None = None
        self._closing = False
        self._heard: list[str] = []
        self._said: list[str] = []
        self._voice_turn_open = False
        self._closing_turn: asyncio.TimerHandle | None = None
        #: Notes and updates for the voice sent while the connection was being
        #: rotated, to send once it is back. Audio is never kept: late
        #: microphone audio is worse than none.
        self._held_text: list[Frame] = []

    async def open(self) -> None:
        await self._connect()
        self._reader = create_inherited_task(self._read(), name="calls.gemini.read")

    def _setup(self) -> Frame:
        generation: Frame = {"responseModalities": ["AUDIO"]}
        if self._voice:
            generation["speechConfig"] = {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}
            }
        setup: Frame = {
            "model": self._model,
            "generationConfig": generation,
            "systemInstruction": {"parts": [{"text": self._instructions}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            # Without it an audio session ends at fifteen minutes.
            "contextWindowCompression": {"slidingWindow": {}},
            "sessionResumption": {"handle": self._handle} if self._handle else {},
        }
        return {"setup": setup}

    async def _connect(self) -> None:
        import websockets

        # Asked on every connect: a service account's token outlives no call.
        headers = await self._headers()
        try:
            socket = await websockets.connect(
                self._endpoint,
                additional_headers=dict(headers),
                max_size=MAX_PROVIDER_FRAME_BYTES,
                open_timeout=_SETUP_TIMEOUT_SECONDS,
            )
            await socket.send(encode(self._setup()))
            async with asyncio.timeout(_SETUP_TIMEOUT_SECONDS):
                while True:
                    reply = decode(await socket.recv())
                    if "setupComplete" in reply:
                        break
        except (OSError, TimeoutError, websockets.WebSocketException) as exc:
            logger.warning("calls.gemini.connect.failed", exc_info=True)
            raise EngineUnavailable("The voice could not connect.") from exc
        self._socket = socket

    async def _send(self, frame: Frame) -> None:
        socket = self._socket
        if socket is None:
            return
        import websockets

        # A send racing a reconnect is dropped: a few milliseconds of
        # microphone is worth less than an error that ends the call.
        with contextlib.suppress(websockets.ConnectionClosed):
            await socket.send(encode(frame))

    async def push_audio(self, pcm: bytes) -> None:
        await self._send(
            {
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(pcm).decode("ascii"),
                        "mimeType": f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                    }
                }
            }
        )

    async def inject(self, text: str, mode: Inject) -> None:
        if mode is Inject.SPEAK:
            await self._send_text({"realtimeInput": {"text": text}})
            return
        await self._send_text(
            {
                "clientContent": {
                    "turns": [{"role": "user", "parts": [{"text": text}]}],
                    "turnComplete": False,
                }
            }
        )

    async def _send_text(self, frame: Frame) -> None:
        """Text for the voice is kept, not dropped, across a reconnect: a
        relay sent while Gemini rotated the connection was never said."""
        socket = self._socket
        import websockets

        if socket is not None and socket.state.name == "OPEN":
            try:
                await socket.send(encode(frame))
                return
            except websockets.ConnectionClosed:
                pass
        self._held_text.append(frame)
        del self._held_text[:-_MAX_HELD_TEXT]

    async def _send_held(self) -> None:
        held, self._held_text = self._held_text, []
        for frame in held:
            await self._send_text(frame)

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, EngineClosed):
                return

    async def close(self) -> None:
        self._closing = True
        if self._closing_turn is not None:
            self._closing_turn.cancel()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._socket is not None:
            await self._socket.close()

    async def _read(self) -> None:
        import websockets

        reconnects = 0
        while not self._closing:
            socket = self._socket
            if socket is None:
                break
            try:
                async for raw in socket:
                    self._handle_frame(decode(raw))
                    reconnects = 0
            except websockets.ConnectionClosed:
                pass
            if self._closing:
                break
            # Closed under us. With a handle the conversation survives a new
            # connection; without one there is nothing to resume.
            if self._handle is None or reconnects >= _MAX_RECONNECTS:
                offer(
                    self._events,
                    EngineClosed("The voice connection dropped. Please call again."),
                    essential=True,
                )
                return
            reconnects += 1
            try:
                await self._connect()
            except EngineUnavailable as exc:
                offer(self._events, EngineClosed(str(exc)), essential=True)
                return
            logger.info("calls.gemini.resumed.observed", attempt=reconnects)
            await self._send_held()
        offer(self._events, EngineClosed(), essential=True)

    def _handle_frame(self, frame: dict[str, JsonValue]) -> None:
        update = frame.get("sessionResumptionUpdate")
        if (
            isinstance(update, dict)
            and update.get("resumable")
            and update.get("newHandle")
        ):
            self._handle = str(update["newHandle"])
        if "goAway" in frame:
            # The connection is about to end; closing it now makes the reader
            # reconnect with the handle rather than wait to be cut off.
            if self._socket is not None:
                create_inherited_task(self._socket.close(), name="calls.gemini.rotate")
            return
        content = frame.get("serverContent")
        if isinstance(content, dict):
            self._handle_content(content)

    def _handle_content(self, content: dict[str, JsonValue]) -> None:
        heard = _text_of(content.get("inputTranscription"))
        if heard:
            if not self._heard:
                offer(self._events, UserSpeaking())
            self._heard.append(heard)
            offer(self._events, Heard(heard, final=False))
            if self._voice_turn_open:
                # Words still arriving after the voice began: the caller's
                # turn is not over until they stop.
                self._close_caller_turn_soon()
        turn = content.get("modelTurn")
        if isinstance(turn, dict):
            self._voice_starts()
            for part in turn.get("parts") or []:
                pcm = _audio_of(part)
                if pcm:
                    offer(self._events, AudioOut(pcm, OUTPUT_SAMPLE_RATE))
        said = _text_of(content.get("outputTranscription"))
        if said:
            self._voice_starts()
            self._said.append(said)
            offer(self._events, Said(said, final=False))
        if content.get("interrupted"):
            offer(self._events, Interrupted())
            self._end_voice_turn()
        if content.get("turnComplete"):
            self._close_caller_turn()
            self._end_voice_turn()

    def _voice_starts(self) -> None:
        # The voice answering is the provider's end-of-turn for the caller, but
        # its transcript runs behind the audio: the last words can land after
        # the first audio frame. The turn closes once they stop arriving.
        if not self._voice_turn_open:
            self._voice_turn_open = True
            self._close_caller_turn_soon()

    def _close_caller_turn_soon(self) -> None:
        if self._closing_turn is not None:
            self._closing_turn.cancel()
        self._closing_turn = asyncio.get_running_loop().call_later(
            CALLER_TURN_GRACE_SECONDS, self._close_caller_turn
        )

    def _close_caller_turn(self) -> None:
        if self._closing_turn is not None:
            self._closing_turn.cancel()
            self._closing_turn = None
        if self._heard:
            text = "".join(self._heard).strip()
            self._heard.clear()
            if text:
                offer(self._events, Heard(text, final=True))

    def _end_voice_turn(self) -> None:
        if not self._voice_turn_open:
            return
        self._voice_turn_open = False
        text = "".join(self._said).strip()
        self._said.clear()
        if text:
            offer(self._events, Said(text, final=True))
        offer(self._events, TurnDone())


def _text_of(value: JsonValue) -> str:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
    return ""


def _audio_of(part: JsonValue) -> bytes:
    if not isinstance(part, dict):
        return b""
    inline = part.get("inlineData")
    if not isinstance(inline, dict):
        return b""
    data = inline.get("data")
    mime = inline.get("mimeType")
    if not isinstance(data, str) or not (isinstance(mime, str) and "pcm" in mime):
        return b""
    return base64.b64decode(data)
