"""One live call: the caller's socket, a voice engine, and the agents behind it.

Four loops run until any of them ends the call:

- **caller** -- microphone audio to the engine; controls (mute, typed text,
  what the canvas is showing them, a canvas item that failed to draw).
- **voice** -- the engine's audio back to the caller, transcripts both ways,
  barge-ins; a finished caller turn is routed.
- **agents** -- what the coordinator relays and what anyone in the call shows on
  the canvas.
- **floor** -- speaks queued relays at the next pause.

Routing never blocks the voice: the gate and the dispatch run beside it, in
order, so the person is answered at voice latency whatever the agents are doing.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from sqlalchemy.exc import SQLAlchemyError

from app.modules.calls.domain.frames import (
    decode,
    encode,
)
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.calls.domain.floor import Floor, Urgency
from app.modules.calls.domain.voice import (
    AudioOut,
    EngineClosed,
    Heard,
    Inject,
    Interrupted,
    Said,
    TurnDone,
    UserSpeaking,
    VoiceEngine,
    VoiceEvent,
)
from app.modules.calls.domain.voice_brief import CANVAS_NOTE, TEAM_NOTE
from app.modules.calls.services.agents import CallAgents, Relay, Shown, Working
from app.modules.calls.services.gate import Route

logger = get_logger(__name__)

_TICK_SECONDS = 0.1
#: How much of what was said goes with a turn the agents get, as context.
_EARLIER_LINES = 12
_RECENT_LINES = 10
_MAX_CONTROL_CHARS = 8000

type Gate = Callable[[str, Sequence[str]], Awaitable[Route]]
type Frame = dict[str, JsonValue]


class CallSocket(Protocol):
    async def send_bytes(self, data: bytes) -> None: ...

    async def send_text(self, data: str) -> None: ...

    async def receive_message(self) -> bytes | str | None:
        """The next frame, or None once the caller has gone."""
        ...


@dataclass(frozen=True, slots=True)
class CallLimits:
    max_seconds: float
    quiet_window_ms: int


#: The background a coordinator is sent with its first turn, at most.
_MAX_BACKGROUND_CHARS = 8_000


@dataclass(slots=True)
class CallStats:
    """What a call carried, for the log line that says how it ended: a call
    that "stopped answering" reads differently with no audio arriving than
    with audio and no turns heard."""

    audio_in_bytes: int = 0
    caller_turns: int = 0
    voice_lines: int = 0


class CallSession:
    def __init__(
        self,
        *,
        socket: CallSocket,
        engine: VoiceEngine,
        agents: CallAgents,
        gate: Gate,
        limits: CallLimits,
        background: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._socket = socket
        self._engine = engine
        self._agents = agents
        self._gate = gate
        self._limits = limits
        self._clock = clock
        self._floor = Floor(quiet_window_ms=limits.quiet_window_ms)
        self._muted = False
        self._working: bool | None = None
        self._lines: list[str] = []
        #: What was said since the agents last got a turn, numbered in order,
        #: so a turn takes only what came before it.
        self._unsent: list[tuple[int, str]] = []
        self._said_count = 0
        self._routing = asyncio.Lock()
        self._titles: dict[str, str] = {}
        self._failures_reported: set[str] = set()
        #: What the voice was told before the call (the pod, the chat it was
        #: started from), told to the agents with their first turn. Without it
        #: the two knew different things: the voice spoke of a chat the
        #: coordinator had never seen, and the coordinator answered as if
        #: nothing had been said.
        self._background = (background or "").strip()[:_MAX_BACKGROUND_CHARS] or None
        self.end_reason: str | None = None
        self.stats = CallStats()

    def _now_ms(self) -> float:
        return self._clock() * 1000

    async def send(self, frame: Frame) -> None:
        await self._socket.send_text(encode(frame))

    async def run(self) -> None:
        await self._replay_canvas()
        loops = [
            create_inherited_task(self._caller(), name="calls.session.caller"),
            create_inherited_task(self._voice(), name="calls.session.voice"),
            create_inherited_task(self._signals(), name="calls.session.agents"),
            create_inherited_task(self._tick(), name="calls.session.floor"),
        ]
        try:
            done, _ = await asyncio.wait(
                loops,
                timeout=self._limits.max_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                self.end_reason = "The call reached its time limit."
        finally:
            for loop in loops:
                loop.cancel()
            for loop in loops:
                with contextlib.suppress(asyncio.CancelledError):
                    await loop
        if self.end_reason:
            await self._say_ended()

    async def _say_ended(self) -> None:
        with contextlib.suppress(OSError, RuntimeError):
            await self.send({"type": "ended", "reason": self.end_reason})

    def transcript(self) -> str:
        return "\n".join(self._lines)

    # -- the caller ----------------------------------------------------------

    async def _caller(self) -> None:
        while True:
            message = await self._socket.receive_message()
            if message is None:
                return
            if isinstance(message, bytes):
                if not self._muted:
                    self.stats.audio_in_bytes += len(message)
                    await self._engine.push_audio(message)
                continue
            if len(message) > _MAX_CONTROL_CHARS:
                continue
            with contextlib.suppress(ValueError):
                control = decode(message)
                if isinstance(control, dict) and await self._control(control):
                    return

    async def _control(self, control: dict[str, JsonValue]) -> bool:
        """Act on one control frame; True ends the call."""
        match control.get("type"):
            case "end":
                return True
            case "mute":
                self._muted = bool(control.get("muted"))
            case "text":
                text = str(control.get("text") or "").strip()
                if text:
                    await self._engine.inject(f"The person typed: {text}", Inject.SPEAK)
                    self._finished_turn(text)
            case "canvas.focus":
                title = self._titles.get(str(control.get("tool_call_id")), "")
                if title:
                    await self._engine.inject(
                        f"{CANVAS_NOTE} The person is looking at: {title}",
                        Inject.CONTEXT,
                    )
            case "canvas.render_failed":
                # Off this loop: the caller's audio keeps flowing while the
                # coordinator is told.
                create_inherited_task(
                    self._render_failed(control), name="calls.session.render_failed"
                )
        return False

    async def _render_failed(self, control: dict[str, JsonValue]) -> None:
        item = str(control.get("tool_call_id") or "")
        error = str(control.get("error") or "")[:500]
        # Once per item: a screen that redraws the canvas on reconnect would
        # otherwise start a coordinator run for the same broken item each time.
        if not item or item in self._failures_reported:
            return
        self._failures_reported.add(item)
        title = self._titles.get(item, "an item")
        try:
            await self._agents.send_turn(
                f"[canvas] '{title}' could not be drawn on the person's screen: "
                f"{error}. Show a corrected one, or something else."
            )
        except DomainError, SQLAlchemyError, OSError:
            logger.warning("calls.session.render_failed.undelivered", exc_info=True)

    # -- the voice -----------------------------------------------------------

    async def _voice(self) -> None:
        async for event in self._engine.events():
            if await self._on_voice(event):
                return

    async def _on_voice(self, event: VoiceEvent) -> bool:
        now = self._now_ms()
        match event:
            case AudioOut(pcm=pcm):
                self._floor.voice_active(now)
                await self._socket.send_bytes(pcm)
            case UserSpeaking():
                self._floor.caller_started(now)
            case Heard(text=text, final=False):
                self._floor.caller_started(now)
                await self.send(
                    {
                        "type": "transcript",
                        "speaker": "caller",
                        "text": text,
                        "final": False,
                    }
                )
            case Heard(text=text, final=True):
                self._floor.caller_finished(now)
                await self.send(
                    {
                        "type": "transcript",
                        "speaker": "caller",
                        "text": text,
                        "final": True,
                    }
                )
                self._finished_turn(text)
            case Said(text=text, final=final):
                await self.send(
                    {
                        "type": "transcript",
                        "speaker": "voice",
                        "text": text,
                        "final": final,
                    }
                )
                if final:
                    self.stats.voice_lines += 1
                    self._remember(f"Voice: {text}")
            case Interrupted():
                await self.send({"type": "clear"})
            case TurnDone():
                self._floor.voice_finished(now)
            case EngineClosed(error=error):
                self.end_reason = error
                return True
        return False

    def _remember(self, line: str) -> int:
        self._lines.append(line)
        self._said_count += 1
        self._unsent.append((self._said_count, line))
        del self._unsent[:-_EARLIER_LINES]
        return self._said_count

    def _finished_turn(self, text: str) -> None:
        self.stats.caller_turns += 1
        recent = self._lines[-_RECENT_LINES:]
        number = self._remember(f"Caller: {text}")
        create_inherited_task(
            self._route(text, recent, number), name="calls.session.route"
        )

    async def _route(self, text: str, recent: Sequence[str], number: int) -> None:
        # In order: a correction must not overtake the request it corrects.
        async with self._routing:
            route = await self._gate(text, recent)
            if route is Route.VOICE:
                return
            # Only what was said before this turn is its context. A turn heard
            # while this one was being routed is still to come, as its own.
            earlier = [line for seq, line in self._unsent if seq < number]
            self._unsent = [(seq, line) for seq, line in self._unsent if seq > number]
            content = f"[caller] {text}"
            if earlier:
                content += (
                    "\n\nEarlier on the call (already handled, or no work needed):\n"
                    + "\n".join(earlier)
                )
            if self._background:
                content += (
                    "\n\nBefore the call, the voice was told:\n" + self._background
                )
            try:
                await self._agents.send_turn(content)
                # Only once delivered: a turn the agents never got leaves the
                # brief for the next one.
                self._background = None
            except DomainError, SQLAlchemyError, OSError:
                # The agents did not get it. The voice is told, so it does not
                # promise work nobody is doing.
                logger.warning("calls.session.dispatch.failed", exc_info=True)
                await self._engine.inject(
                    f"{TEAM_NOTE} Your team did not receive the last request; "
                    "nothing is being done about it. Ask the person to say it again.",
                    Inject.CONTEXT,
                )

    # -- the agents ----------------------------------------------------------

    async def _signals(self) -> None:
        async for signal in self._agents.signals():
            match signal:
                case Relay(text=text, urgency="context"):
                    await self._engine.inject(f"{TEAM_NOTE} {text}", Inject.CONTEXT)
                case Relay(text=text, urgency=urgency):
                    level = Urgency.NOW if urgency == "now" else Urgency.WHEN_IDLE
                    self._floor.queue(text, level)
                case Shown():
                    await self._shown(signal)
                case Working(running=running) if running != self._working:
                    self._working = running
                    await self.send({"type": "work", "running": running})

    async def _shown(self, shown: Shown) -> None:
        title, caption = _describe(shown)
        self._titles[shown.tool_call_id] = title
        await self.send({"type": "canvas.item", "item": shown.as_frame()})
        note = f"{CANVAS_NOTE} Now on the person's screen: {title}"
        await self._engine.inject(
            note + (f" -- {caption}" if caption else ""), Inject.CONTEXT
        )

    async def _replay_canvas(self) -> None:
        """A caller reconnecting gets the canvas as it stands, and the voice is
        told what that screen now shows -- the newest item, with the rest a
        page back -- so it never speaks of a screen the person is not seeing."""
        items = await self._agents.canvas()
        described: list[tuple[str, str]] = []
        for item in items:
            shown = Shown(
                conversation_id=item.conversation_id,
                tool_call_id=item.tool_call_id,
                tool_name=item.tool_name,
                args=item.args,
                result=item.result,
            )
            title, caption = _describe(shown)
            self._titles[item.tool_call_id] = title
            described.append((title, caption))
            await self.send(
                {"type": "canvas.item", "item": shown.as_frame(), "replay": True}
            )
        if described:
            await self._engine.inject(_on_screen(described), Inject.CONTEXT)

    # -- the floor -----------------------------------------------------------

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(_TICK_SECONDS)
            update = self._floor.take(self._now_ms())
            if update:
                await self._engine.inject(f"{TEAM_NOTE} {update}", Inject.SPEAK)


def _describe(shown: Shown) -> tuple[str, str]:
    """A canvas item's title and the line the voice is told about it.

    The line is the item's `fallback`: the plain-text summary every surface
    that cannot render gets, which a voice is one of.
    """
    args = shown.args if isinstance(shown.args, dict) else {}
    raw = args.get("data")
    # A widget's data may arrive as JSON text; the display decodes it too.
    data = decode(raw) if isinstance(raw, str) else raw
    data = data if isinstance(data, dict) else {}
    path = args.get("path")
    title = (
        args.get("name")
        or data.get("title")
        or (str(path).rsplit("/", 1)[-1] if path else None)
        or str(args.get("type") or "").lower()
    )
    caption = args.get("fallback") or data.get("caption") or ""
    return str(title or "an item"), str(caption)


def _on_screen(described: Sequence[tuple[str, str]]) -> str:
    """The note that tells a resumed voice what the person's screen shows."""
    title, caption = described[-1]
    note = f"{CANVAS_NOTE} Back on the call, the person's screen shows: {title}"
    if caption:
        note += f" -- {caption}"
    earlier = [each for each, _ in described[:-1]]
    if earlier:
        note += ". They can page back to: " + "; ".join(earlier[-10:])
    return note + "."
