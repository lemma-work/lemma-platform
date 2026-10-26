"""One live call: the caller's socket, a voice engine, and the agents behind it.

Five loops run until any of them ends the call:

- **caller** -- microphone audio to the engine; controls (mute, typed text,
  what the canvas is showing them, a canvas item that failed to draw).
- **voice** -- the engine's audio back to the caller, transcripts both ways,
  barge-ins; a finished caller turn is routed.
- **agents** -- what the coordinator relays, what anyone in the call shows on
  the canvas, and whether anyone is working, failed or finished.
- **floor** -- speaks queued relays at the next pause.
- **guard** -- that this connection still holds the call, that the person's
  budget still allows it, and that a run the streams lost track of is not
  still being reported as going.

Routing never blocks the voice: the gate and the dispatch run beside it, in
order, so the person is answered at voice latency whatever the agents are doing.

The voice is told facts, never left to infer them: that a request reached the
team, that the team is working or not, that it finished or failed, what is on
the screen (``narration``). A loop that fails ends the call with a reason, and
every task the call starts is finished or cancelled before it is over.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import JsonValue
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.calls.domain.floor import Floor, Urgency
from app.modules.calls.domain.frames import decode, encode
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
from app.modules.calls.domain.voice_brief import TEAM_NOTE
from app.modules.calls.services import narration
from app.modules.calls.services.agents import (
    CallAgents,
    Failed,
    Finished,
    Relay,
    Shown,
    Working,
)
from app.modules.calls.services.gate import Route

logger = get_logger(__name__)

_TICK_SECONDS = 0.1
_GUARD_SECONDS = 2.0
#: How often a run still reported as going is checked against the store.
_WORKING_RECHECK_SECONDS = 15.0
#: How often a long call's budget is checked again.
_BUDGET_SECONDS = 300.0
#: How long the call's own tasks get to finish once it is over.
_DRAIN_SECONDS = 2.0
#: A turn that takes longer than this to reach the agents is called lost.
_DISPATCH_SECONDS = 30.0
#: How much of what was said goes with a turn the agents get, as context.
_EARLIER_LINES = 12
_RECENT_LINES = 10
#: Lines kept back before they are written into the conversation, so a call
#: that ends abruptly loses at most this many.
_FLUSH_LINES = 8
_MAX_CONTROL_CHARS = 8000

TRANSCRIPT_HEADING = "Said on the call -- by the person and the voice, not by you"

type Gate = Callable[[str, Sequence[str]], Awaitable[Route]]
type Frame = dict[str, JsonValue]


class CallSocket(Protocol):
    async def send_bytes(self, data: bytes) -> None: ...

    async def send_text(self, data: str) -> None: ...

    async def receive_message(self) -> bytes | str | None:
        """The next frame, or None once the caller has gone."""
        ...


class Holding(Protocol):
    """Whether this connection still holds the call (``lease.CallLease``)."""

    async def still_held(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class CallLimits:
    #: What is left of the call's time, over all its connections.
    max_seconds: float
    quiet_window_ms: int


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
        holding: Holding | None = None,
        check_budget: Callable[[], Awaitable[None]] | None = None,
        muted: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._socket = socket
        self._engine = engine
        self._agents = agents
        self._gate = gate
        self._limits = limits
        self._holding = holding
        self._check_budget = check_budget
        self._clock = clock
        self._floor = Floor(quiet_window_ms=limits.quiet_window_ms)
        self._muted = muted
        self._working: bool | None = None
        self._lines: list[str] = []
        self._flushed = 0
        #: What was said since the agents last got a turn, numbered in order,
        #: so a turn takes only what came before it.
        self._unsent: list[tuple[int, str]] = []
        self._said_count = 0
        self._routing = asyncio.Lock()
        self._titles: dict[str, str] = {}
        #: Canvas items this connection has put on the screen.
        self._on_canvas: set[str] = set()
        #: Of those, the ones replayed from before it connected.
        self._replayed: set[str] = set()
        self._failures_reported: set[str] = set()
        #: Refusals already written into the conversation, so a call refused
        #: on every turn says so once rather than after each.
        self._noted: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.end_reason: str | None = None
        self.stats = CallStats()

    def _now_ms(self) -> float:
        return self._clock() * 1000

    async def send(self, frame: Frame) -> None:
        await self._socket.send_text(encode(frame))

    def _spawn(self, work: Coroutine[Any, Any, None], name: str) -> None:
        """A task of this call's: logged if it fails, finished or cancelled
        before the call is over."""
        task = create_inherited_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "calls.session.task.failed",
                task=task.get_name(),
                exc_info=task.exception(),
            )

    async def run(self) -> None:
        # Subscribed before anything is read back, so what is shown or relayed
        # in between is not lost to the gap.
        await self._agents.start()
        await self._replay_canvas()
        loops = [
            create_inherited_task(self._caller(), name="calls.session.caller"),
            create_inherited_task(self._voice(), name="calls.session.voice"),
            create_inherited_task(self._signals(), name="calls.session.agents"),
            create_inherited_task(self._tick(), name="calls.session.floor"),
            create_inherited_task(self._guard(), name="calls.session.guard"),
        ]
        try:
            done, _ = await asyncio.wait(
                loops,
                timeout=self._limits.max_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                self.end_reason = "The call reached its time limit."
            for loop in done:
                self._loop_ended(loop)
        finally:
            for loop in loops:
                loop.cancel()
            # Gathered with their exceptions: a loop that failed has been
            # logged above, and must not stop the others winding down.
            await asyncio.gather(*loops, return_exceptions=True)
            await self._drain()
        if self.end_reason:
            await self._say_ended()

    def _loop_ended(self, loop: asyncio.Task[None]) -> None:
        """A loop that raised ends the call with a reason, never in silence."""
        if loop.cancelled() or loop.exception() is None:
            return
        logger.warning(
            "calls.session.loop.failed", loop=loop.get_name(), exc_info=loop.exception()
        )
        self.end_reason = self.end_reason or "Something went wrong on the call."

    async def _drain(self) -> None:
        """The call's own tasks -- a turn being routed, a note being written --
        get a moment to finish, then are cancelled: none outlives the call."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=_DRAIN_SECONDS)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _say_ended(self) -> None:
        with contextlib.suppress(OSError, RuntimeError):
            await self.send({"type": "ended", "reason": self.end_reason})

    def transcript(self) -> str:
        return "\n".join(self._lines)

    async def flush(self, *, final: bool = False) -> None:
        """Write what was said since the last flush into the conversation.

        As the call goes rather than only at its end: a call the process dies
        under keeps all but the last few lines, and the conversation shows the
        call while it happens."""
        chunk = self._take_unflushed(final=final)
        if chunk:
            await self._write(chunk)

    def _take_unflushed(self, *, final: bool) -> list[str]:
        """The lines not yet written, cut now: what a write takes is fixed
        when it is asked for, not when it gets to run."""
        pending = self._lines[self._flushed :]
        if not pending or (not final and len(pending) < _FLUSH_LINES):
            return []
        self._flushed = len(self._lines)
        return pending

    async def _write(self, chunk: list[str]) -> None:
        try:
            await self._agents.record(TRANSCRIPT_HEADING + "\n\n" + "\n".join(chunk))
        except DomainError, SQLAlchemyError, OSError:
            logger.warning("calls.session.transcript.failed", exc_info=True)

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
                        narration.looking_at(title), Inject.CONTEXT
                    )
            case "canvas.render_failed":
                # Off this loop: the caller's audio keeps flowing while the
                # coordinator is told.
                self._spawn(
                    self._render_failed(control), name="calls.session.render_failed"
                )
        return False

    async def _render_failed(self, control: dict[str, JsonValue]) -> None:
        item = str(control.get("tool_call_id") or "")
        error = str(control.get("error") or "")[:500]
        # Once per item, and never for one replayed from before this
        # connection: that one was reported when it was first drawn, and each
        # reconnect would otherwise start another coordinator run for it.
        if not item or item in self._failures_reported or item in self._replayed:
            return
        self._failures_reported.add(item)
        title = self._titles.get(item, "an item")
        # In order with the person's turns: a repair must not overtake them.
        async with self._routing:
            try:
                async with asyncio.timeout(_DISPATCH_SECONDS):
                    await self._agents.send_turn(
                        f"[canvas] '{title}' could not be drawn on the person's "
                        f"screen: {error}. Show a corrected one, or something else."
                    )
            except DomainError, SQLAlchemyError, OSError, TimeoutError:
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
                await self._caption("caller", text, final=False)
            case Heard(text=text, final=True):
                self._floor.caller_finished(now)
                await self._caption("caller", text, final=True)
                self._finished_turn(text)
            case Said(text=text, final=final):
                await self._caption("voice", text, final=final)
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

    async def _caption(self, speaker: str, text: str, *, final: bool) -> None:
        await self.send(
            {"type": "transcript", "speaker": speaker, "text": text, "final": final}
        )

    def _remember(self, line: str) -> int:
        self._lines.append(line)
        self._said_count += 1
        self._unsent.append((self._said_count, line))
        del self._unsent[:-_EARLIER_LINES]
        chunk = self._take_unflushed(final=False)
        if chunk:
            self._spawn(self._write(chunk), name="calls.session.flush")
        return self._said_count

    def _finished_turn(self, text: str) -> None:
        self.stats.caller_turns += 1
        recent = self._lines[-_RECENT_LINES:]
        number = self._remember(f"Caller: {text}")
        self._spawn(self._route(text, recent, number), name="calls.session.route")

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
                    "\n\nSaid on the call since your last turn (not sent to you):\n"
                    + "\n".join(earlier)
                )
            try:
                async with asyncio.timeout(_DISPATCH_SECONDS):
                    await self._agents.send_turn(content)
            except DomainError as exc:
                # Refused for a reason that saying it again will not change --
                # a model this person cannot use, a spending limit. The voice
                # is told the reason to pass on; asked only to "say it again",
                # it had a person repeat themselves for a whole call.
                logger.warning("calls.session.dispatch.failed", exc_info=True)
                await self._refused(exc.message)
                await self._note(
                    f"Your team could not take requests on this call: {exc.message}",
                    once=exc.message,
                )
                return
            except SQLAlchemyError, OSError, TimeoutError:
                # The agents did not get it, this once. The voice is told, so
                # it does not promise work nobody is doing.
                logger.warning("calls.session.dispatch.failed", exc_info=True)
                await self._engine.inject(narration.not_received(), Inject.CONTEXT)
                await self._note(
                    f'A request on this call did not reach your team: "{text}"'
                )
                return
            # Now, and only now, may the voice say it is being looked into.
            await self._engine.inject(narration.passed_on(text), Inject.CONTEXT)

    async def _note(self, text: str, *, once: str | None = None) -> None:
        """Write what went wrong into the call's conversation, to read later."""
        if once is not None:
            if once in self._noted:
                return
            self._noted.add(once)
        try:
            await self._agents.note(text)
        except DomainError, SQLAlchemyError, OSError:
            # The same store that just failed may fail again; the voice and
            # the screen have already said it.
            logger.warning("calls.session.note.failed", exc_info=True)

    async def _refused(self, reason: str) -> None:
        await self._engine.inject(narration.refused(reason), Inject.CONTEXT)
        await self._notice(reason)

    async def _notice(self, text: str) -> None:
        """On the screen too: a reason heard once in a call is easy to miss."""
        with contextlib.suppress(OSError, RuntimeError):
            await self.send({"type": "notice", "text": text})

    # -- the agents ----------------------------------------------------------

    async def _signals(self) -> None:
        async for signal in self._agents.signals():
            match signal:
                case Relay(text=text, urgency="context"):
                    await self._engine.inject(f"{TEAM_NOTE} {text}", Inject.CONTEXT)
                case Relay(text=text, urgency=urgency):
                    self._queue(
                        f"{TEAM_NOTE} {text}",
                        Urgency.NOW if urgency == "now" else Urgency.WHEN_IDLE,
                    )
                case Shown():
                    await self._shown(signal)
                case Working(running=running) if running != self._working:
                    self._working = running
                    await self.send({"type": "work", "running": running})
                    await self._engine.inject(
                        narration.team_working(running), Inject.CONTEXT
                    )
                case Failed(reason=reason):
                    self._queue(narration.team_failed(reason), Urgency.NOW)
                    await self._notice(reason)
                    await self._note(f"Your team's work on this call failed: {reason}")
                case Finished(answer=answer):
                    self._queue(narration.team_finished(answer), Urgency.WHEN_IDLE)

    def _queue(self, text: str, urgency: Urgency) -> None:
        for dropped in self._floor.queue(text, urgency, self._now_ms()):
            logger.warning("calls.session.update.dropped", update=dropped[:200])

    async def _shown(self, shown: Shown) -> None:
        if shown.tool_call_id in self._on_canvas:
            # Replayed already, and heard again on the stream it was shown on.
            return
        self._on_canvas.add(shown.tool_call_id)
        title, caption = narration.describe(shown.args)
        self._titles[shown.tool_call_id] = title
        await self.send({"type": "canvas.item", "item": shown.as_frame()})
        await self._engine.inject(
            narration.now_on_screen(title, caption), Inject.CONTEXT
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
            title, caption = narration.describe(shown.args)
            self._titles[item.tool_call_id] = title
            self._on_canvas.add(item.tool_call_id)
            self._replayed.add(item.tool_call_id)
            described.append((title, caption))
            await self.send(
                {"type": "canvas.item", "item": shown.as_frame(), "replay": True}
            )
        if described:
            await self._engine.inject(narration.on_screen(described), Inject.CONTEXT)

    # -- the floor -----------------------------------------------------------

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(_TICK_SECONDS)
            update = self._floor.take(self._now_ms())
            if update:
                await self._engine.inject(update, Inject.SPEAK)

    # -- the guard -----------------------------------------------------------

    async def _guard(self) -> None:
        """Ends the call when it is held elsewhere or out of budget, and
        corrects a run the streams lost track of."""
        last_recheck = last_budget = self._clock()
        while True:
            await asyncio.sleep(_GUARD_SECONDS)
            if self._holding is not None and not await self._holding.still_held():
                self.end_reason = "This call continued somewhere else."
                return
            now = self._clock()
            if self._check_budget is not None and now - last_budget >= _BUDGET_SECONDS:
                last_budget = now
                try:
                    await self._check_budget()
                except DomainError as exc:
                    self.end_reason = exc.message
                    return
            if self._working and now - last_recheck >= _WORKING_RECHECK_SECONDS:
                last_recheck = now
                await self._recheck_working()

    async def _recheck_working(self) -> None:
        try:
            running = await self._agents.still_working()
        except DomainError, SQLAlchemyError, OSError, RedisError:
            logger.warning("calls.session.recheck.failed", exc_info=True)
            return
        if not running and self._working:
            self._working = False
            await self.send({"type": "work", "running": False})
            await self._engine.inject(narration.team_working(False), Inject.CONTEXT)
