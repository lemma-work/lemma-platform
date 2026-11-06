"""What the calls module does to a conversation: hold a call in it, speak into
it, keep its transcript and notes, and read the call's canvas and its earlier
connections back.

A turn on a call is an ordinary inbound message, so it steers the
coordinator's run when one is going and starts one when not. What is new is
only that a conversation can hold a call -- a new one, or the chat the call
was started from -- through its ``call`` column, and that a call's canvas spans
the coordinator's conversation and its sub-agents' (``infrastructure/db/calls``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue

from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.conversations.start import StartConversation
from app.modules.agent.application.turns.send_message import SendMessage
from app.modules.agent.domain.run_kind import RunKind
from app.modules.agent.domain.calls import CallMark, CallRole
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.errors import (
    ConversationNotFoundError,
    ConversationStateError,
)
from app.modules.agent.domain.value_objects import (
    ConversationOrigin,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.db.calls import CALL_TRANSCRIPT_KEY, Calls
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.conversation_access import resolve_agent
from app.modules.agent.services.runtime_availability import (
    ensure_turn_runtime_available,
    turn_runtime,
)


@dataclass(frozen=True, slots=True)
class OpenedCall:
    conversation_id: UUID
    title: str | None


@dataclass(frozen=True, slots=True)
class CanvasItem:
    """One thing shown on a call's canvas: the tool call that showed it."""

    conversation_id: UUID
    tool_call_id: str
    tool_name: str
    args: JsonValue
    result: JsonValue
    shown_at: datetime


async def open_call(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    title: str,
    agent_name: str | None = None,
    conversation_id: UUID | None = None,
    runtime: AgentRuntimeConfig | None = None,
) -> OpenedCall:
    """The conversation a call is held in.

    ``conversation_id`` holds it in that chat -- the person's own, in this pod
    -- so the call, the agent's work and the transcript are the one chat they
    were in; a call already held there is picked up again. Without one, a new
    conversation. ``runtime`` pins a new conversation's coordinator to a model
    (its sub-agents inherit it); an existing chat keeps the model it has.

    Refused, with the reason a turn would give, when the caller cannot run the
    model the coordinator would run on -- another member's own Claude Code, a
    retired model. Checked here because every turn of the call needs it: left
    to the first turn, the voice took call after call while every request
    behind it failed, and it could only ask the person to say it again.

    The coordinator's brief is not written into the conversation: it rides in
    each run's context while the call is live (``context_brief.lines``), so
    the chat reads as itself again once the call ends.

    The caller sets the authorization context.
    """
    conversations = Conversations(uow)
    if conversation_id is None:
        conversation = await StartConversation.build(uow).start(
            pod_id=pod_id,
            agent_name=agent_name,
            user_id=user_id,
            title=title,
            agent_runtime=runtime,
            origin=ConversationOrigin.CALL,
        )
    else:
        conversation = await conversations.get_conversation(conversation_id)
        if (
            conversation is None
            or conversation.user_id != user_id
            or conversation.pod_id != pod_id
        ):
            raise ConversationNotFoundError()
        if (
            conversation.parent_id is not None
            or conversation.channel_id is not None
            or conversation.contact_id is not None
        ):
            raise ConversationStateError(
                "A call can only be held in your own chat with a teammate."
            )
    agent = await resolve_agent(
        conversation, user_id=user_id, agent_repository=AgentRepository(uow)
    )
    await ensure_turn_runtime_available(
        uow,
        await turn_runtime(uow, conversation, agent),
        organization_id=conversation.organization_id,
        user_id=user_id,
    )
    existing = conversation.call
    if existing is not None and existing.role is not CallRole.COORDINATOR:
        raise ConversationStateError("A sub-agent's conversation cannot hold a call.")
    if existing is None:
        # Only a new mark is written. One already there was read before the
        # awaits above, and a hang-up in between may have updated it since:
        # writing it back would mark the call live again and lose its time.
        await conversations.set_call(
            conversation.id,
            CallMark(
                call_id=conversation.id,
                role=CallRole.COORDINATOR,
                since=datetime.now(UTC),
            ),
        )
    return OpenedCall(conversation_id=conversation.id, title=conversation.title)


async def held_call(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> CallMark | None:
    """The call's mark as it stands."""
    return await Conversations(uow).get_call(call_id)


async def set_call_live(
    uow: SqlAlchemyUnitOfWork, call_id: UUID, *, live: bool, held_for: float = 0.0
) -> None:
    """Mark the call held (a socket has it) or not, adding ``held_for`` seconds
    to its running total. While live the coordinator gets the call's brief
    and its voice tool; once not, the chat reads as itself again."""
    conversations = Conversations(uow)
    mark = await conversations.get_call(call_id)
    if mark is None:
        return
    await conversations.set_call(
        call_id,
        replace(mark, live=live, held_seconds=mark.held_seconds + max(held_for, 0.0)),
    )


async def owned_call(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
) -> bool:
    """Whether this is a call's own conversation, in this pod, owned by this person."""
    return await Calls(uow).owned(
        conversation_id=conversation_id, user_id=user_id, pod_id=pod_id
    )


async def retire_empty_call(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> bool:
    """Archive a call's conversation that nothing was ever said in.

    A call that never connected -- the microphone refused, the tab closed while
    it was opening -- still made a conversation, and it sat in the chat list as
    another "Call with ..." to open and find empty. Only one with no message
    and no sub-agent at all, so a call anything happened in is never hidden.
    """
    return await Calls(uow).retire_if_empty(call_id)


async def call_transcripts(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[str]:
    """What was said on the call's earlier connections, oldest first.

    Each connection records its transcript in the call's conversation as it
    goes, marked ``call_transcript``; a call resumed after a drop or a hang-up
    reads them back so the voice knows what it already said.
    """
    return await Calls(uow).transcripts(call_id)


async def call_conversations(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[UUID]:
    """The call's own conversation and every sub-agent's at any depth, oldest
    first."""
    return await Calls(uow).conversations(call_id)


async def call_working(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> bool:
    """Whether anyone in the call -- the coordinator or a sub-agent -- has a
    run going, read when the voice cannot have been told."""
    calls = Calls(uow)
    return await calls.working(await calls.conversations(call_id))


async def missed_relays(
    uow: SqlAlchemyUnitOfWork, call_id: UUID
) -> list[tuple[str, str]]:
    """What the coordinator relayed to a voice nobody was connected to.

    Since the call's last transcript was written, which is when its last
    connection ended: ``(text, urgency)``, oldest first. A relay goes out over
    live events only, so one sent while the line was down was never heard.
    """
    return await Calls(uow).relays_since_last_transcript(call_id)


async def speak_into_call(
    uow: SqlAlchemyUnitOfWork,
    *,
    call_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    content: str,
) -> None:
    """What the caller said, as a turn of the call's conversation."""
    await SendMessage.build(uow).send(
        conversation_id=call_id,
        user_id=user_id,
        content=content,
        pod_id=pod_id,
        message_metadata={"source": "call"},
        kind=RunKind.CALL,
    )


async def keep_in_call(
    uow: SqlAlchemyUnitOfWork, *, call_id: UUID, text: str, metadata: dict[str, str]
) -> None:
    """A line kept in the call's conversation, in no run: part of its
    transcript (``call_transcript``) or a note about something that went wrong."""
    await Messages(uow).append_message(
        conversation_id=call_id,
        agent_run_id=None,
        draft=MessageDraft.of_notification(text, metadata=metadata),
    )


async def call_canvas(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[CanvasItem]:
    """Everything shown on the call's canvas so far, in the order it was shown:
    since the conversation became a call, so a chat's own earlier displays,
    never on a call's screen, are not put there now."""
    calls = Calls(uow)
    mark = await held_call(uow, call_id)
    shown_calls: dict[str, Message] = {}
    results: dict[str, JsonValue] = {}
    for message in await calls.canvas_messages(
        await calls.conversations(call_id),
        since=mark.since if mark is not None else None,
    ):
        if not message.tool_call_id:
            continue
        if message.kind is MessageKind.TOOL_CALL:
            shown_calls.setdefault(message.tool_call_id, message)
        else:
            results[message.tool_call_id] = message.tool_result
    return [
        CanvasItem(
            conversation_id=message.conversation_id,
            tool_call_id=tool_call_id,
            tool_name=message.tool_name or "",
            args=message.tool_args,
            result=results.get(tool_call_id),
            shown_at=message.created_at,
        )
        for tool_call_id, message in shown_calls.items()
        if _shown(results.get(tool_call_id))
    ]


def _shown(result: JsonValue) -> bool:
    """A display that succeeded: the same test the live call applies."""
    return isinstance(result, dict) and bool(result.get("success"))


__all__ = [
    "CALL_TRANSCRIPT_KEY",
    "CanvasItem",
    "OpenedCall",
    "call_canvas",
    "call_conversations",
    "call_transcripts",
    "call_working",
    "held_call",
    "keep_in_call",
    "missed_relays",
    "open_call",
    "owned_call",
    "retire_empty_call",
    "set_call_live",
    "speak_into_call",
]
