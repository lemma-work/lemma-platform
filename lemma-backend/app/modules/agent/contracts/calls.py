"""What the calls module does to a conversation: open one as a call, and read
the call's canvas back.

A turn on a call is an ordinary inbound message (``conversations_for_surfaces.
start_surface_turn``), so it steers the coordinator's run when one is going and
starts one when not. What is new is only that a conversation can be a call, and
that a call's canvas spans the coordinator's conversation and its sub-agents'.

A submodule rather than ``contracts/__init__``, which is a leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import exists, or_, select, update
from sqlalchemy.orm import aliased

from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.calls import (
    CALL_METADATA_KEY,
    CANVAS_TOOL_NAMES,
    CallMark,
    CallRole,
    call_mark,
    coordinator_brief,
)
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.service_factories import build_conversation_service


#: Sub-agents one call may draw from. A coordinator spawns one per distinct
#: ask; a call that has spawned more than this has stopped being a call.
MAX_CALL_CONVERSATIONS = 200


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
    service = build_conversation_service(uow)
    conversation = await service.create_conversation(
        pod_id=pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=title,
        instructions=coordinator_brief(agent_display_name=agent_display_name),
        agent_runtime=runtime,
    )
    mark = CallMark(call_id=conversation.id, role=CallRole.COORDINATOR)
    await ConversationRepository(uow).set_conversation_metadata_key(
        conversation.id, CALL_METADATA_KEY, mark.as_metadata()
    )
    return OpenedCall(conversation_id=conversation.id, title=conversation.title)


async def owned_call(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
) -> bool:
    """Whether this is a call's own conversation, in this pod, owned by this person."""
    row = (
        await uow.session.execute(
            select(
                ConversationModel.user_id,
                ConversationModel.pod_id,
                ConversationModel.conversation_metadata,
            ).where(ConversationModel.id == conversation_id)
        )
    ).one_or_none()
    if row is None or row.user_id != user_id or row.pod_id != pod_id:
        return False
    mark = call_mark(row.conversation_metadata)
    return mark is not None and mark.role is CallRole.COORDINATOR


async def retire_empty_call(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> bool:
    """Archive a call's conversation that nothing was ever said in.

    A call that never connected -- the microphone refused, the tab closed while
    it was opening -- still made a conversation, and it sat in the chat list as
    another "Call with ..." to open and find empty. Only one with no message
    and no sub-agent at all, so a call anything happened in is never hidden.
    """
    child = aliased(ConversationModel)
    result = await uow.session.execute(
        update(ConversationModel)
        .where(
            ConversationModel.id == call_id,
            ~exists().where(MessageModel.conversation_id == call_id),
            ~exists().where(child.parent_id == call_id),
        )
        .values(is_archived=True)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def call_conversations(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[UUID]:
    """The call's own conversation, then each sub-agent's, oldest first."""
    rows = await uow.session.execute(
        select(ConversationModel.id)
        .where(
            or_(ConversationModel.id == call_id, ConversationModel.parent_id == call_id)
        )
        .order_by(ConversationModel.created_at)
        .limit(MAX_CALL_CONVERSATIONS)
    )
    return list(rows.scalars())


async def call_canvas(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[CanvasItem]:
    """Everything shown on the call's canvas so far, in the order it was shown."""
    conversations = await call_conversations(uow, call_id)
    rows = (
        await uow.session.execute(
            select(MessageModel)
            .where(
                MessageModel.conversation_id.in_(conversations),
                MessageModel.tool_name.in_(CANVAS_TOOL_NAMES),
                MessageModel.kind.in_(
                    [MessageKind.TOOL_CALL.value, MessageKind.TOOL_RETURN.value]
                ),
            )
            .order_by(MessageModel.created_at, MessageModel.sequence)
        )
    ).scalars()
    calls: dict[str, MessageModel] = {}
    results: dict[str, JsonValue] = {}
    for row in rows:
        if not row.tool_call_id:
            continue
        if row.kind == MessageKind.TOOL_CALL.value:
            calls.setdefault(row.tool_call_id, row)
        else:
            results[row.tool_call_id] = row.tool_result
    return [
        CanvasItem(
            conversation_id=row.conversation_id,
            tool_call_id=tool_call_id,
            tool_name=row.tool_name or "",
            args=row.tool_args,
            result=results.get(tool_call_id),
            shown_at=row.created_at,
        )
        for tool_call_id, row in calls.items()
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
    "open_call",
    "owned_call",
]
