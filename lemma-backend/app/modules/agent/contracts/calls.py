"""What the calls module does to a conversation: open one as a call, speak
into it, keep its transcript, and read its canvas back.

A call is a conversation whose origin is ``CALL`` and whose ``call`` column
names it the coordinator. A turn on it is an ordinary message, so it steers the
coordinator's run when one is going and starts one when not; its canvas spans
the coordinator's conversation and its sub-agents' (``infrastructure/db/calls``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.conversations.start import StartConversation
from app.modules.agent.application.turns.send_message import SendMessage
from app.modules.agent.domain.calls import CallMark, CallRole, coordinator_brief
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    ConversationOrigin,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.db.calls import Calls
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages


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
    agent_name: str | None,
    agent_display_name: str,
    user_id: UUID,
    title: str,
    runtime: AgentRuntimeConfig | None = None,
) -> OpenedCall:
    """A new conversation that is a call, with the coordinator's brief.

    ``runtime`` pins the coordinator to a model; its sub-agents inherit it.
    The caller sets the authorization context.
    """
    conversation = await StartConversation.build(uow).start(
        pod_id=pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=title,
        instructions=coordinator_brief(agent_display_name=agent_display_name),
        agent_runtime=runtime,
        origin=ConversationOrigin.CALL,
    )
    # Named by its own id, which the row has only once it is written.
    await Conversations(uow).set_call(
        conversation.id, CallMark(call_id=conversation.id, role=CallRole.COORDINATOR)
    )
    return OpenedCall(conversation_id=conversation.id, title=conversation.title)


async def owned_call(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
) -> bool:
    """Whether this is a call's own conversation, in this pod, owned by this person."""
    return await Calls(uow).owned(
        conversation_id=conversation_id, user_id=user_id, pod_id=pod_id
    )


async def call_conversations(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[UUID]:
    """The call's own conversation, then each sub-agent's, oldest first."""
    return await Calls(uow).conversations(call_id)


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
    )


async def keep_transcript(
    uow: SqlAlchemyUnitOfWork, *, call_id: UUID, transcript: str, key: str
) -> None:
    """The call's transcript, kept in its conversation, in no run."""
    await Messages(uow).append_message(
        conversation_id=call_id,
        agent_run_id=None,
        draft=MessageDraft.of_notification(
            transcript, metadata={"call_transcript": key}
        ),
    )


async def call_canvas(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[CanvasItem]:
    """Everything shown on the call's canvas so far, in the order it was shown."""
    calls = Calls(uow)
    shown_calls: dict[str, Message] = {}
    results: dict[str, JsonValue] = {}
    for message in await calls.canvas_messages(await calls.conversations(call_id)):
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
    "CanvasItem",
    "OpenedCall",
    "call_canvas",
    "call_conversations",
    "keep_transcript",
    "open_call",
    "owned_call",
    "speak_into_call",
]
