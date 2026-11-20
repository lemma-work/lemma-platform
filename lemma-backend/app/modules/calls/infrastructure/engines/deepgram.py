"""Deepgram's two streaming sockets: Flux listens, a Deepgram voice speaks.

Flux is a transcriber that also decides when the caller's turn ended, which is
the part a cascade most often gets wrong: silence-based endpointing either cuts
people off mid-thought or leaves a long gap after they finish. It reports an
eager end of turn first, so the talker can start drafting before the confirmed
one, and takes it back with ``TurnResumed`` if the caller keeps going.

``flux-general-multi`` hears ten languages, Hindi among them, switches with
the caller mid-sentence, and says which it heard on every turn; that picks the
voice the reply is spoken in.

The speaker holds one socket for the whole call. An Aura voice (``aura-2-*``)
speaks on ``/v1/speak``; a Flux voice (``flux-*``, Hindi among its languages)
on ``/v2/speak``, where ``Clear`` is called ``Interrupt``. Text goes in a sentence at a time
and ``Flush`` marks the end of a reply; ``Clear`` drops whatever has not been
spoken, which is what a barge-in needs.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from app.modules.calls.domain.frames import (
    MAX_PROVIDER_FRAME_BYTES,
    decode,
    encode,
)
from app.core.log.log import get_logger
from app.modules.calls.domain.voice import INPUT_SAMPLE_RATE, EngineUnavailable

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

logger = get_logger(__name__)

_LISTEN = "wss://api.deepgram.com/v2/listen"
_SPEAK = "wss://api.deepgram.com/v1/speak"
_SPEAK_FLUX = "wss://api.deepgram.com/v2/speak"
_OPEN_TIMEOUT_SECONDS = 10.0
SPEAK_SAMPLE_RATE = 24_000


@dataclass(frozen=True, slots=True)
class TurnInfo:
    """One Flux turn event: ``StartOfTurn``, ``Update``, ``EagerEndOfTurn``,
    ``TurnResumed`` or ``EndOfTurn``."""

    event: str
    transcript: str
    #: The languages heard this turn, most words first (``flux-general-multi``
    #: only).
    languages: tuple[str, ...] = ()


def _languages(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    codes: list[str] = []
    for entry in value:
        code = entry.get("language") if isinstance(entry, dict) else entry
        if isinstance(code, str) and code:
            codes.append(code)
    return tuple(codes)


def speaks_on_flux(voice: str) -> bool:
    """Flux voices are served on ``/v2/speak`` only, Aura voices on ``/v1``."""
    return voice.startswith("flux-")


async def _open(url: str, api_key: str) -> ClientConnection:
    import websockets

    try:
        return await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {api_key}"},
            open_timeout=_OPEN_TIMEOUT_SECONDS,
            max_size=MAX_PROVIDER_FRAME_BYTES,
        )
    except (OSError, TimeoutError, websockets.WebSocketException) as exc:
        logger.warning("calls.deepgram.connect.failed", exc_info=True)
        raise EngineUnavailable("Speech could not connect.") from exc


class FluxListener:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        eager_eot_threshold: float | None,
        language_hints: tuple[str, ...] = (),
        endpoint: str = _LISTEN,
    ) -> None:
        params: list[tuple[str, str]] = [
            ("model", model),
            ("encoding", "linear16"),
            ("sample_rate", str(INPUT_SAMPLE_RATE)),
        ]
        if eager_eot_threshold is not None:
            params.append(("eager_eot_threshold", str(eager_eot_threshold)))
        # Only the multilingual model takes hints; any other answers 400.
        if model == "flux-general-multi":
            params.extend(("language_hint", hint) for hint in language_hints)
        self._url = f"{endpoint}?{urlencode(params)}"
        self._api_key = api_key
        self._socket: ClientConnection | None = None

    async def open(self) -> None:
        self._socket = await _open(self._url, self._api_key)

    async def send(self, pcm: bytes) -> None:
        if self._socket is None:
            return
        import websockets

        with contextlib.suppress(websockets.ConnectionClosed):
            await self._socket.send(pcm)

    async def turns(self) -> AsyncIterator[TurnInfo]:
        """Turn events until the socket closes."""
        if self._socket is None:
            return
        import websockets

        with contextlib.suppress(websockets.ConnectionClosed):
            async for raw in self._socket:
                if not isinstance(raw, str):
                    continue
                frame = decode(raw)
                if frame.get("type") != "TurnInfo":
                    continue
                yield TurnInfo(
                    event=str(frame.get("event") or ""),
                    transcript=str(frame.get("transcript") or ""),
                    languages=_languages(frame.get("languages")),
                )

    async def close(self) -> None:
        if self._socket is not None:
            import websockets

            with contextlib.suppress(websockets.ConnectionClosed):
                await self._socket.send(encode({"type": "CloseStream"}))
            await self._socket.close()


class DeepgramSpeaker:
    """One speaking socket. Audio arrives on ``audio()``; each ``Flush`` is
    answered by one ``Flushed``, counted so a reply knows when it was heard out.

    The voice can change between replies (``use_voice``): the socket is
    reopened on the next one, on the endpoint that voice is served from."""

    def __init__(
        self, *, api_key: str, voice: str, endpoint: str | None = None
    ) -> None:
        self._endpoint = endpoint
        self.voice = voice
        self._url = self._url_for(voice)
        self._api_key = api_key
        self._socket: ClientConnection | None = None
        self._lock = asyncio.Lock()
        self._opened = asyncio.Event()
        self._closing = False
        self.flushed = asyncio.Condition()
        self.flush_count = 0

    def _url_for(self, voice: str) -> str:
        params = {
            "model": voice,
            "encoding": "linear16",
            "sample_rate": str(SPEAK_SAMPLE_RATE),
        }
        endpoint = self._endpoint or (_SPEAK_FLUX if speaks_on_flux(voice) else _SPEAK)
        return f"{endpoint}?{urlencode(params)}"

    @property
    def _turn_done(self) -> str:
        """What says a reply's audio has all been sent: ``Flushed`` on Aura.
        On Flux audio can still follow ``Flushed``; ``SpeechMetadata`` closes
        the turn, and a voice switch after it cuts nothing off."""
        return "SpeechMetadata" if speaks_on_flux(self.voice) else "Flushed"

    async def open(self) -> None:
        self._socket = await _open(self._url, self._api_key)
        self._opened.set()

    async def use_voice(self, voice: str) -> None:
        """Speak the next reply in ``voice``. Called between replies only: the
        socket is closed, and opened again with the new voice by the next
        ``speak``."""
        if voice == self.voice:
            return
        async with self._lock:
            self.voice = voice
            self._url = self._url_for(voice)
            socket, self._socket = self._socket, None
            self._opened.clear()
        if socket is not None:
            import websockets

            with contextlib.suppress(websockets.ConnectionClosed):
                await socket.close()

    async def _send(self, frame: dict[str, str]) -> None:
        async with self._lock:
            if self._socket is None or self._socket.state.name != "OPEN":
                # Aura closes an idle socket; a reply after a long silence
                # reopens it rather than failing the call.
                await self.open()
            assert self._socket is not None
            await self._socket.send(encode(frame))

    async def speak(self, text: str) -> None:
        await self._send({"type": "Speak", "text": text})

    async def flush(self) -> None:
        await self._send({"type": "Flush"})

    async def clear(self) -> None:
        # The same thing under the name each endpoint knows it by.
        kind = "Interrupt" if speaks_on_flux(self.voice) else "Clear"
        await self._send({"type": kind})

    async def audio(self) -> AsyncIterator[bytes]:
        import websockets

        while not self._closing:
            await self._opened.wait()
            socket = self._socket
            if socket is None:
                if self._closing:
                    return
                # Between voices: wait for the next one to open.
                self._opened.clear()
                continue
            with contextlib.suppress(websockets.ConnectionClosed):
                async for raw in socket:
                    if isinstance(raw, bytes):
                        yield raw
                    elif decode(raw).get("type") == self._turn_done:
                        async with self.flushed:
                            self.flush_count += 1
                            self.flushed.notify_all()
            # Ended under us; `_send` reopens it on the next reply.
            if socket is self._socket:
                self._opened.clear()

    async def close(self) -> None:
        self._closing = True
        self._opened.set()
        if self._socket is not None:
            import websockets

            with contextlib.suppress(websockets.ConnectionClosed):
                await self._socket.send(encode({"type": "Close"}))
            await self._socket.close()
