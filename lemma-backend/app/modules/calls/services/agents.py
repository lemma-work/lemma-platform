"""The call's side of its agents: turns go in, relays and canvas items come out.

Turns are ordinary inbound messages on the call's conversation, so a turn that
arrives while the coordinator is running steers that run rather than queueing
a second. What comes back is read off the conversations' own event streams --
the coordinator's and each sub-agent's -- because a tool call is already a
persisted message there: nothing in the worker needs to know which replica
holds the caller's socket.
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
    """Whether the coordinator has a run going."""

    running: bool


type AgentSignal = Relay | Shown | Working
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


class CallAgents:
    def __init__(
        self,
        *,
        call_id: UUID,
        party: Party,
        stream: Callable[[UUID], ConversationFrames] = _conversation_stream,
    ) -> None:
        self.call_id = call_id
        self._party = party
        self._stream = stream
        self._signals: asyncio.Queue[AgentSignal] = asyncio.Queue(maxsize=EVENT_BACKLOG)
        self._watching: dict[UUID, asyncio.Task[None]] = {}
        self._pending_args: dict[str, JsonValue] = {}

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
        """The call's transcript, kept in its conversation once it ends."""
        async with as_caller(self._party) as scope:
            await append_delivery_notice(
                scope.uow,
                conversation_id=self.call_id,
                message=transcript,
                metadata={CALL_TRANSCRIPT_KEY: str(uuid4())},
            )

    async def signals(self) -> AsyncIterator[AgentSignal]:
        async with as_caller(self._party) as scope:
            known = await call_conversations(scope.uow, self.call_id)
        for conversation_id in known:
            self._watch(conversation_id)
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
        try:
            async with self._stream(conversation_id) as frames:
                async for frame in frames:
                    self._read(conversation_id, frame)
        except RedisError, OSError:
            # The call carries on without this conversation's relays and
            # canvas items; the voice is told rather than left waiting.
            logger.warning(
                "calls.agents.follow.failed",
                conversation_id=str(conversation_id),
                exc_info=True,
            )
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
        is_call = conversation_id == self.call_id
        if is_call and kind in _RUN_ENDINGS:
            offer(self._signals, Working(running=False))
            return
        if is_call and kind == "status":
            offer(self._signals, Working(running=True))
            return
        data = frame.get("data")
        if kind == "message" and isinstance(data, dict):
            self._read_message(conversation_id, data)

    def _read_message(self, conversation_id: UUID, data: dict[str, JsonValue]) -> None:
        tool = data.get("tool_name")
        call_id = data.get("tool_call_id")
        if not isinstance(tool, str) or not isinstance(call_id, str):
            return
        if data.get("kind") == "TOOL_CALL":
            self._on_call(conversation_id, tool, call_id, data.get("tool_args"))
        elif data.get("kind") == "TOOL_RETURN":
            self._on_return(conversation_id, tool, call_id, data.get("tool_result"))

    def _on_call(
        self, conversation_id: UUID, tool: str, call_id: str, args: JsonValue
    ) -> None:
        if (
            tool == _RELAY_TOOL
            and conversation_id == self.call_id
            and isinstance(args, dict)
        ):
            text, urgency = args.get("text"), args.get("urgency") or "when_idle"
            if isinstance(text, str) and text.strip():
                offer(self._signals, Relay(text=text.strip(), urgency=str(urgency)))
        elif tool in _CANVAS_TOOLS:
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
