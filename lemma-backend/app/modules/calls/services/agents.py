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
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import JsonValue
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.modules.calls.domain.frames import (
    EVENT_BACKLOG,
    offer,
)
from app.core.authorization.scope import context_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
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
    missed_relays,
)
from app.modules.agent.contracts.conversations_for_surfaces import (
    append_delivery_notice,
    start_surface_turn,
)

logger = get_logger(__name__)

_CONVERSATION_STREAM = "agent.conversation"
_CANVAS_TOOLS = frozenset({"display_resource"})
_RELAY_TOOL = "relay_to_voice"
_SPAWN_TOOL = "spawn_subagent"
_RUN_ENDINGS = frozenset({"completed", "error", "stopped"})
#: Attempts at a stream that keeps failing before the call says so.
_FOLLOW_ATTEMPTS = 5
_FOLLOW_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class Relay:
    text: str
    urgency: str


@dataclass(frozen=True, slots=True)
class Shown:
    """Something put on the canvas: the tool call that showed it."""

    conversation_id: UUID
    tool_call_id: str
    tool_name: str
    args: JsonValue
    result: JsonValue

    def as_frame(self) -> dict[str, JsonValue]:
        return {
            "conversation_id": str(self.conversation_id),
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class Working:
    """Whether anyone in the call -- the coordinator or a sub-agent -- has a
    run going."""

    running: bool


@dataclass(frozen=True, slots=True)
class Failed:
    """The coordinator's run failed; ``reason`` is what to tell the person."""

    reason: str


@dataclass(frozen=True, slots=True)
class Finished:
    """The coordinator's run ended without relaying or showing anything.

    ``answer`` is its last written words, if any: the voice would otherwise
    keep saying the work is in hand."""

    answer: str | None


type AgentSignal = Relay | Shown | Working | Failed | Finished


@dataclass(slots=True)
class _RunState:
    """What one coordinator run has done that the call can hear of."""

    updated: bool = False
    answer: str | None = None


type ConversationFrames = AbstractAsyncContextManager[
    AsyncIterator[dict[str, JsonValue]]
]


@dataclass(frozen=True, slots=True)
class Party:
    user_id: UUID
    pod_id: UUID


@dataclass(frozen=True, slots=True)
class CallerScope:
    uow: SqlAlchemyUnitOfWork
    organization_id: UUID | None


@asynccontextmanager
async def as_caller(party: Party) -> AsyncIterator[CallerScope]:
    """One short unit of work, authorized as the person on the call."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=party.user_id, pod_id=party.pod_id
        )
        async with context_scope(ctx):
            yield CallerScope(uow=uow, organization_id=ctx.organization_id)


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

    async def send_turn(self, content: str) -> None:
        async with as_caller(self._party) as scope:
            await start_surface_turn(
                scope.uow,
                conversation_id=self.call_id,
                user_id=self._party.user_id,
                pod_id=self._party.pod_id,
                content=content,
                message_metadata={"source": "call"},
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
            await append_delivery_notice(
                scope.uow,
                conversation_id=self.call_id,
                message=transcript,
                metadata={CALL_TRANSCRIPT_KEY: str(uuid4())},
            )

    async def note(self, text: str) -> None:
        """Something that went wrong on the call, kept in its conversation.

        A call is heard, not read: a refusal the voice said once is gone with
        the call. Written into the conversation, it is still there to read --
        and a call with something in it is never archived as empty.
        """
        async with as_caller(self._party) as scope:
            await append_delivery_notice(
                scope.uow,
                conversation_id=self.call_id,
                message=text,
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

    async def still_working(self) -> bool:
        """Whether a run is going, read from the store: a stream can lose the
        frame that ended one."""
        async with as_caller(self._party) as scope:
            return await call_working(scope.uow, self.call_id)

    async def signals(self) -> AsyncIterator[AgentSignal]:
        while True:
            yield await self._signals.get()

    async def close(self) -> None:
        for task in self._watching.values():
            task.cancel()
        # Gathered, not awaited one by one: a watcher that failed must not
        # stop the call from closing the rest or recording its transcript.
        await asyncio.gather(*self._watching.values(), return_exceptions=True)

    def _watch(self, conversation_id: UUID) -> None:
        if conversation_id in self._watching:
            return
        self._watching[conversation_id] = create_inherited_task(
            self._follow(conversation_id), name="calls.agents.follow"
        )

    async def _follow(self, conversation_id: UUID) -> None:
        """Follow one conversation for the whole call: a stream that ends or
        fails is subscribed to again, and only one that keeps failing is given
        up on -- with the voice told, rather than left waiting."""
        failures = 0
        while failures < _FOLLOW_ATTEMPTS:
            try:
                async with self._stream(conversation_id) as frames:
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

    def _read(self, conversation_id: UUID, frame: dict[str, JsonValue]) -> None:
        kind = frame.get("type")
        run_id = frame.get("agent_run_id")
        is_call = conversation_id == self.call_id
        if kind == "status":
            self._started(conversation_id, run_id if is_call else None)
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
            self._ended(conversation_id, run_id if is_call else None)
            return
        data = frame.get("data")
        if kind == "message" and isinstance(data, dict):
            self._read_message(conversation_id, data)

    def _started(self, conversation_id: UUID, run_id: JsonValue) -> None:
        if isinstance(run_id, str):
            self._runs.setdefault(run_id, _RunState())
        self._set_running(conversation_id, True)

    def _ended(self, conversation_id: UUID, run_id: JsonValue) -> None:
        if isinstance(run_id, str):
            state = self._runs.pop(run_id, None)
            if state is not None and not state.updated:
                offer(self._signals, Finished(answer=state.answer), essential=True)
        self._set_running(conversation_id, False)

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
            if isinstance(text, str) and text.strip():
                self._updated(data)
                offer(self._signals, Relay(text=text.strip(), urgency=str(urgency)))
        elif tool in _CANVAS_TOOLS:
            if conversation_id == self.call_id:
                self._updated(data)
            self._pending_args[call_id] = args

    def _on_return(
        self, conversation_id: UUID, tool: str, call_id: str, result: JsonValue
    ) -> None:
        if tool == _SPAWN_TOOL and isinstance(result, dict):
            child = result.get("conversation_id")
            if isinstance(child, str):
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
