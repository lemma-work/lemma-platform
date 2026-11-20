"""The call's side of its agents: turns go in, relays and canvas items come out.

Turns are ordinary inbound messages on the call's conversation, so a turn that
arrives while the coordinator is running steers that run rather than queueing
a second. What comes back is read off the conversations' own event streams --
the coordinator's and each sub-agent's -- because a tool call is already a
persisted message there: nothing in the worker needs to know which replica
holds the caller's socket.

Those streams are live only: what was sent while nobody listened is gone. So
the call subscribes before it reads anything back, re-reads what a reconnect
could have missed (the canvas, relays, whether anything is running), and a
stream that fails or ends is subscribed to again rather than left silent.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import JsonValue
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.modules.calls.domain.frames import (
    EVENT_BACKLOG,
    offer,
)
from app.core.domain.errors import DomainError
from app.core.live.topics import live_registry
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.agent.contracts.calls import (
    CALL_TRANSCRIPT_KEY,
    CanvasItem,
    call_canvas,
    call_conversations,
    call_transcripts,
    call_working,
    keep_in_call,
    missed_relays,
    relays_since,
    speak_into_call,
)

from app.modules.calls.services.signals import (
    AgentSignal,
    Failed,
    Finished,
    Relay,
    RunStarted,
    Shown,
    Working,
)

from app.modules.calls.services.caller import Party, as_caller

logger = get_logger(__name__)

_CONVERSATION_STREAM = "agent.conversation"
_CANVAS_TOOLS = frozenset({"display_resource"})
_RELAY_TOOL = "relay_to_voice"
#: The calls that start sub-agents: `delegate` lists each child it started,
#: the deprecated `spawn_subagent` returns its one.
_SPAWN_TOOLS = frozenset({"delegate", "spawn_subagent"})
_RUN_ENDINGS = frozenset({"completed", "error", "stopped"})
#: After a spawning tool call, when to look for the conversations it made:
#: ``delegate`` waiting on its children returns only once they are done, so
#: the return cannot be what names them while they work.
_DISCOVER_AFTER_SECONDS = (1.0, 4.0, 10.0)
#: How long a coordinator run that called a spawning tool is given, after it
#: ends, for a child run to be heard starting: past the second discovery look.
_SPAWN_SETTLE_SECONDS = 5.0
#: How long the call waits for its streams to be subscribed before reading
#: back what it missed. Past it the read goes ahead: a slow subscription costs
#: a frame or two, a call that never starts costs the call.
_SUBSCRIBE_WAIT_SECONDS = 5.0
#: How far before a stream was found dropped a catch-up reads back. A drop is
#: noticed late -- the failure surfaces after frames already went missing --
#: and what it reads twice is told apart: relays by id, the canvas by item.
_CATCH_UP_MARGIN = timedelta(seconds=30)
#: Attempts at a stream that keeps failing before the call says so.
_FOLLOW_ATTEMPTS = 5
_FOLLOW_BACKOFF_SECONDS = 1.0


def _children_started(result: dict[str, object]) -> list[str]:
    """The child conversations a spawning call's return names."""
    named = [result.get("conversation_id")]
    children = result.get("children")
    if isinstance(children, list):
        named.extend(
            child.get("conversation_id")
            for child in children
            if isinstance(child, dict)
        )
    return [child for child in named if isinstance(child, str)]


@dataclass(slots=True)
class _RunState:
    """What one coordinator run has done that the call can hear of."""

    updated: bool = False
    answer: str | None = None
    #: It started sub-agents: their reports come back to it, so its own end
    #: is not the end of the work.
    spawned: bool = False
    #: Child runs heard starting in the call before its first spawning call:
    #: any after it are taken as its own.
    children_before: int = 0


type ConversationFrames = AbstractAsyncContextManager[
    AsyncIterator[dict[str, JsonValue]]
]


def _conversation_stream(conversation_id: UUID) -> ConversationFrames:
    opener = live_registry().stream(_CONVERSATION_STREAM)
    if opener is None:
        raise RuntimeError("The agent module's conversation stream is not installed.")
    return opener(str(conversation_id))


#: Marks a note the call wrote into its conversation about something that
#: went wrong, beside the transcript's ``call_transcript``.
CALL_NOTICE_KEY = "call_notice"


class CallAgents:
    def __init__(
        self,
        *,
        call_id: UUID,
        party: Party,
        stream: Callable[[UUID], ConversationFrames] = _conversation_stream,
        retry_seconds: float = _FOLLOW_BACKOFF_SECONDS,
    ) -> None:
        self.call_id = call_id
        self._party = party
        self._stream = stream
        self._retry_seconds = retry_seconds
        self._signals: asyncio.Queue[AgentSignal] = asyncio.Queue(maxsize=EVENT_BACKLOG)
        self._watching: dict[UUID, asyncio.Task[None]] = {}
        self._pending_args: dict[str, JsonValue] = {}
        #: Conversations in the call with a run going, by their stream.
        self._running: set[UUID] = set()
        self._runs: dict[str, _RunState] = {}
        #: Runs heard going, each with its conversation, and runs heard end.
        #: Any frame of a run not yet heard of starts it: the default runtime
        #: publishes no ``status`` frame, so waiting for one never saw a run.
        self._live: dict[str, UUID] = {}
        self._over: set[str] = set()
        self._discovering: asyncio.Task[None] | None = None
        #: Set once each conversation's stream is subscribed.
        self._subscribed: dict[UUID, asyncio.Event] = {}
        #: Relays heard live, by tool call: a catch-up says only the others.
        self._heard_relays: set[str] = set()
        self._catching_up: set[asyncio.Task[None]] = set()
        #: Moves with every run heard starting or ending.
        self._observed = 0
        #: Child runs heard starting, counted: a coordinator run that called
        #: a spawning tool is "finished" only if no child started after it.
        self._children_started = 0

    async def send_turn(self, content: str) -> None:
        async with as_caller(self._party) as scope:
            await speak_into_call(
                scope.uow,
                call_id=self.call_id,
                user_id=self._party.user_id,
                pod_id=self._party.pod_id,
                content=content,
            )

    async def earlier(self) -> list[str]:
        """Transcripts of this call's earlier connections, oldest first."""
        async with as_caller(self._party) as scope:
            return await call_transcripts(scope.uow, self.call_id)

    async def canvas(self) -> list[CanvasItem]:
        async with as_caller(self._party) as scope:
            return await call_canvas(scope.uow, self.call_id)

    async def record(self, transcript: str) -> None:
        """Part of the call's transcript, kept in its conversation as it goes."""
        async with as_caller(self._party) as scope:
            await keep_in_call(
                scope.uow,
                call_id=self.call_id,
                text=transcript,
                metadata={CALL_TRANSCRIPT_KEY: str(uuid4())},
            )

    async def note(self, text: str) -> None:
        """Something that went wrong on the call, kept in its conversation.

        A call is heard, not read: a refusal the voice said once is gone with
        the call. Written into the conversation, it is still there to read --
        and a call with something in it is never archived as empty.
        """
        async with as_caller(self._party) as scope:
            await keep_in_call(
                scope.uow,
                call_id=self.call_id,
                text=text,
                metadata={CALL_NOTICE_KEY: str(uuid4())},
            )

    async def start(self) -> None:
        """Subscribe to everyone in the call, then read back what a reconnect
        could have missed: relays sent while nobody listened, and whether
        anything is still running. Before the canvas is replayed, so nothing
        shown in between falls through the gap."""
        async with as_caller(self._party) as scope:
            known = await call_conversations(scope.uow, self.call_id)
        for conversation_id in known:
            self._watch(conversation_id)
        # Subscribed first, really: ``_watch`` only starts the task that
        # subscribes, and a read made before it lands loses what is published
        # in between.
        waits = [self._subscribed[conversation_id].wait() for conversation_id in known]
        try:
            async with asyncio.timeout(_SUBSCRIBE_WAIT_SECONDS):
                await asyncio.gather(*waits)
        except TimeoutError:
            logger.warning("calls.agents.subscribe.slow", call_id=str(self.call_id))
        async with as_caller(self._party) as scope:
            missed = await missed_relays(scope.uow, self.call_id)
            working = await call_working(scope.uow, self.call_id)
        for text, urgency in missed:
            # Said at the next pause, never cutting in: it is already late.
            offer(
                self._signals,
                Relay(
                    text=text,
                    urgency="context" if urgency == "context" else "when_idle",
                ),
                essential=True,
            )
        offer(self._signals, Working(running=working), essential=True)

    async def discover(self) -> None:
        """Watch every conversation the store says is in the call: a child
        made by a tool whose return has not come back is found here."""
        async with as_caller(self._party) as scope:
            known = await call_conversations(scope.uow, self.call_id)
        for conversation_id in known:
            self._watch(conversation_id)

    async def _discover_soon(self) -> None:
        waited = 0.0
        for at in _DISCOVER_AFTER_SECONDS:
            await asyncio.sleep(at - waited)
            waited = at
            try:
                await self.discover()
            except DomainError, SQLAlchemyError, OSError:
                logger.warning("calls.agents.discover.failed", exc_info=True)

    async def still_working(self) -> bool:
        """Whether a run is going, read from the store: a stream can lose the
        frame that ended one, or never carry the one that started it."""
        await self.discover()
        seen = self._observed
        async with as_caller(self._party) as scope:
            running = await call_working(scope.uow, self.call_id)
        if seen != self._observed:
            # A run started or ended while the store was read: the frames are
            # newer than the read, so they stand.
            return running or bool(self._live)
        if not running:
            # The frames that ended those runs were lost; forgetting them lets
            # the next run start report itself as working again.
            self._running.clear()
            self._over.update(self._live)
            self._live.clear()
        return running

    async def signals(self) -> AsyncIterator[AgentSignal]:
        while True:
            yield await self._signals.get()

    async def close(self) -> None:
        if self._discovering is not None:
            self._discovering.cancel()
        for task in list(self._catching_up):
            task.cancel()
        for task in self._watching.values():
            task.cancel()
        # Gathered, not awaited one by one: a watcher that failed must not
        # stop the call from closing the rest or recording its transcript.
        await asyncio.gather(*self._watching.values(), return_exceptions=True)

    def _watch(self, conversation_id: UUID) -> None:
        if conversation_id in self._watching:
            return
        self._subscribed[conversation_id] = asyncio.Event()
        self._watching[conversation_id] = create_inherited_task(
            self._follow(conversation_id), name="calls.agents.follow"
        )

    async def _follow(self, conversation_id: UUID) -> None:
        """Follow one conversation for the whole call: a stream that ends or
        fails is subscribed to again, and only one that keeps failing is given
        up on -- with the voice told, rather than left waiting."""
        failures = 0
        dropped_at: datetime | None = None
        while failures < _FOLLOW_ATTEMPTS:
            try:
                async with self._stream(conversation_id) as frames:
                    self._subscribed[conversation_id].set()
                    if dropped_at is not None:
                        # Back after a drop: what was published while the
                        # stream was down went nowhere, so read it back --
                        # from the first drop, however many retries failed.
                        self._catch_up_soon(conversation_id, dropped_at)
                        dropped_at = None
                    async for frame in frames:
                        failures = 0
                        self._read(conversation_id, frame)
            except RedisError, OSError, SQLAlchemyError, RuntimeError:
                failures += 1
                logger.warning(
                    "calls.agents.follow.failed",
                    conversation_id=str(conversation_id),
                    exc_info=True,
                )
            if dropped_at is None:
                dropped_at = datetime.now(UTC)
            await asyncio.sleep(self._retry_seconds * max(failures, 1))
        self._running.discard(conversation_id)
        offer(
            self._signals,
            Relay(
                text="Updates from part of your team stopped arriving; "
                "do not promise results from it.",
                urgency="when_idle",
            ),
            essential=True,
        )

    def _catch_up_soon(self, conversation_id: UUID, dropped_at: datetime) -> None:
        task = create_inherited_task(
            self._catch_up(conversation_id, dropped_at - _CATCH_UP_MARGIN),
            name="calls.agents.catch_up",
        )
        self._catching_up.add(task)
        task.add_done_callback(self._catching_up.discard)

    async def _read_back(
        self, conversation_id: UUID, since: datetime
    ) -> tuple[list[tuple[str, str, str]], list[CanvasItem], bool]:
        """What a stream may have missed since ``since``: the call's relays
        (only its own conversation relays), its canvas, and whether anyone in
        it is working."""
        async with as_caller(self._party) as scope:
            relays = (
                await relays_since(scope.uow, self.call_id, since)
                if conversation_id == self.call_id
                else []
            )
            canvas = await call_canvas(scope.uow, self.call_id)
        return relays, canvas, await self.still_working()

    async def _catch_up(self, conversation_id: UUID, since: datetime) -> None:
        try:
            relays, canvas, working = await self._read_back(conversation_id, since)
        except DomainError, SQLAlchemyError, OSError, RedisError:
            logger.warning(
                "calls.agents.catch_up.failed",
                conversation_id=str(conversation_id),
                exc_info=True,
            )
            return
        for tool_call_id, text, urgency in relays:
            if tool_call_id in self._heard_relays:
                continue
            self._heard_relays.add(tool_call_id)
            # Late already: said at the next pause, never cutting in.
            offer(
                self._signals,
                Relay(
                    text=text,
                    urgency="context" if urgency == "context" else "when_idle",
                ),
                essential=True,
            )
        # The session puts each item up once, so the ones already shown are
        # passed over there.
        for item in canvas:
            offer(
                self._signals,
                Shown(
                    conversation_id=item.conversation_id,
                    tool_call_id=item.tool_call_id,
                    tool_name=item.tool_name,
                    args=item.args,
                    result=item.result,
                ),
            )
        offer(self._signals, Working(running=working), essential=True)

    def _read(self, conversation_id: UUID, frame: dict[str, JsonValue]) -> None:
        kind = frame.get("type")
        run_id = frame.get("agent_run_id")
        is_call = conversation_id == self.call_id
        if kind not in _RUN_ENDINGS:
            if isinstance(run_id, str):
                if run_id not in self._live and run_id not in self._over:
                    self._started(conversation_id, run_id)
            elif kind == "status":
                self._set_running(conversation_id, True)
        if kind == "status":
            return
        if kind == "error" and is_call:
            reason = frame.get("data")
            offer(
                self._signals,
                Failed(reason=str(reason) if reason else "The work failed."),
                essential=True,
            )
            if isinstance(run_id, str):
                self._runs.setdefault(run_id, _RunState()).updated = True
        if kind in _RUN_ENDINGS:
            self._ended(conversation_id, run_id)
            return
        data = frame.get("data")
        if kind == "message" and isinstance(data, dict):
            self._read_message(conversation_id, data)

    def _started(self, conversation_id: UUID, run_id: str) -> None:
        self._observed += 1
        self._live[run_id] = conversation_id
        if conversation_id != self.call_id:
            self._children_started += 1
        if conversation_id == self.call_id:
            self._runs.setdefault(run_id, _RunState())
            offer(self._signals, RunStarted(agent_run_id=run_id), essential=True)
        self._set_running(conversation_id, True)

    def _ended(self, conversation_id: UUID, run_id: JsonValue) -> None:
        if isinstance(run_id, str):
            self._observed += 1
            self._live.pop(run_id, None)
            self._over.add(run_id)
            state = (
                self._runs.pop(run_id, None)
                if conversation_id == self.call_id
                else None
            )
            if state is not None and not state.updated:
                if state.spawned:
                    # A spawning tool was called; whether a child started is
                    # known only once discovery has had its look.
                    self._finish_unless_children(state)
                else:
                    offer(self._signals, Finished(answer=state.answer), essential=True)
        # Another run of the same conversation may still be going.
        if conversation_id not in self._live.values():
            self._set_running(conversation_id, False)

    def _finish_unless_children(self, state: _RunState) -> None:
        async def decide() -> None:
            await asyncio.sleep(_SPAWN_SETTLE_SECONDS)
            # Its own children only: one an earlier run started, still busy,
            # is not this run's, and must not swallow this run's answer.
            if self._children_started == state.children_before:
                # It called the tool, but no child started: its own words are
                # all there is, so they reach the person.
                offer(self._signals, Finished(answer=state.answer), essential=True)

        task = create_inherited_task(decide(), name="calls.agents.finish_unless")
        self._catching_up.add(task)
        task.add_done_callback(self._catching_up.discard)

    def _set_running(self, conversation_id: UUID, running: bool) -> None:
        before = bool(self._running)
        if running:
            self._running.add(conversation_id)
        else:
            self._running.discard(conversation_id)
        if bool(self._running) != before:
            offer(self._signals, Working(running=bool(self._running)), essential=True)

    def _read_message(self, conversation_id: UUID, data: dict[str, JsonValue]) -> None:
        if conversation_id == self.call_id:
            self._note_answer(data)
        tool = data.get("tool_name")
        call_id = data.get("tool_call_id")
        if not isinstance(tool, str) or not isinstance(call_id, str):
            return
        if data.get("kind") == "TOOL_CALL":
            self._on_call(conversation_id, tool, call_id, data.get("tool_args"), data)
        elif data.get("kind") == "TOOL_RETURN":
            self._on_return(conversation_id, tool, call_id, data.get("tool_result"))

    def _note_answer(self, data: dict[str, JsonValue]) -> None:
        """Keep the coordinator's last written words, for a run that ends
        without relaying them."""
        run_id = data.get("agent_run_id")
        text = data.get("text")
        if (
            isinstance(run_id, str)
            and data.get("role") == "assistant"
            and data.get("kind") == "TEXT"
            and isinstance(text, str)
            and text.strip()
        ):
            self._runs.setdefault(run_id, _RunState()).answer = text.strip()

    def _updated(self, data: dict[str, JsonValue]) -> None:
        run_id = data.get("agent_run_id")
        if isinstance(run_id, str):
            self._runs.setdefault(run_id, _RunState()).updated = True

    def _on_call(
        self,
        conversation_id: UUID,
        tool: str,
        call_id: str,
        args: JsonValue,
        data: dict[str, JsonValue],
    ) -> None:
        if (
            tool == _RELAY_TOOL
            and conversation_id == self.call_id
            and isinstance(args, dict)
        ):
            text, urgency = args.get("text"), args.get("urgency") or "when_idle"
            if call_id in self._heard_relays:
                # A catch-up after a drop already said it.
                return
            self._heard_relays.add(call_id)
            if isinstance(text, str) and text.strip():
                self._updated(data)
                offer(self._signals, Relay(text=text.strip(), urgency=str(urgency)))
        elif tool in _CANVAS_TOOLS:
            if conversation_id == self.call_id:
                self._updated(data)
            self._pending_args[call_id] = args
        elif tool in _SPAWN_TOOLS:
            run_id = data.get("agent_run_id")
            if conversation_id == self.call_id and isinstance(run_id, str):
                state = self._runs.setdefault(run_id, _RunState())
                if not state.spawned:
                    state.spawned = True
                    state.children_before = self._children_started
            if self._discovering is None or self._discovering.done():
                self._discovering = create_inherited_task(
                    self._discover_soon(), name="calls.agents.discover"
                )

    def _on_return(
        self, conversation_id: UUID, tool: str, call_id: str, result: JsonValue
    ) -> None:
        if tool in _SPAWN_TOOLS and isinstance(result, dict):
            for child in _children_started(result):
                with contextlib.suppress(ValueError):
                    self._watch(UUID(child))
            return
        if tool not in _CANVAS_TOOLS:
            return
        args = self._pending_args.pop(call_id, None)
        if not isinstance(result, dict) or not result.get("success") or args is None:
            return
        offer(
            self._signals,
            Shown(
                conversation_id=conversation_id,
                tool_call_id=call_id,
                tool_name=tool,
                args=args,
                result=result,
            ),
        )
