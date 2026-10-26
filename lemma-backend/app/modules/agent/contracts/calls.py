"""What the calls module does to a conversation: hold a call in it, and read
the call's canvas and its earlier connections back.

A turn on a call is an ordinary inbound message (``conversations_for_surfaces.
start_surface_turn``), so it steers the coordinator's run when one is going and
starts one when not. What is new is only that a conversation can hold a call --
a new one, or the chat the call was started from -- and that a call's canvas
spans the coordinator's conversation and its sub-agents'.

A submodule rather than ``contracts/__init__``, which is a leaf.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
    RELAY_TOOL_NAME,
    CallRole,
    call_mark,
)
from app.modules.agent.domain.errors import (
    ConversationNotFoundError,
    ConversationStateError,
)
from app.modules.agent.domain.value_objects import AgentRunStatus, MessageKind
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.repositories.agent_repository import (
    AgentRepository,
)
from app.modules.agent.services.conversation_access import resolve_agent
from app.modules.agent.services.runtime_availability import (
    ensure_turn_runtime_available,
    turn_runtime,
)
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

    The caller sets the authorization context.
    """
    repository = ConversationRepository(uow)
    if conversation_id is None:
        conversation = await build_conversation_service(uow).create_conversation(
            pod_id=pod_id,
            agent_name=agent_name,
            user_id=user_id,
            title=title,
            agent_runtime=runtime,
        )
    else:
        conversation = await repository.get_conversation(conversation_id)
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
    existing = call_mark(conversation.metadata)
    if existing is not None and existing.role is not CallRole.COORDINATOR:
        raise ConversationStateError("A sub-agent's conversation cannot hold a call.")
    mark = existing or CallMark(
        call_id=conversation.id, role=CallRole.COORDINATOR, since=datetime.now(UTC)
    )
    await repository.set_conversation_metadata_key(
        conversation.id, CALL_METADATA_KEY, mark.as_metadata()
    )
    return OpenedCall(conversation_id=conversation.id, title=conversation.title)


async def held_call(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> CallMark | None:
    """The call's mark as it stands."""
    metadata = await uow.session.scalar(
        select(ConversationModel.conversation_metadata).where(
            ConversationModel.id == call_id
        )
    )
    return call_mark(metadata)


async def set_call_live(
    uow: SqlAlchemyUnitOfWork, call_id: UUID, *, live: bool, held_for: float = 0.0
) -> None:
    """Mark the call held (a socket has it) or not, adding ``held_for`` seconds
    to its running total. While live the coordinator gets the call's brief
    and its voice tool; once not, the chat reads as itself again."""
    mark = await held_call(uow, call_id)
    if mark is None:
        return
    await ConversationRepository(uow).set_conversation_metadata_key(
        call_id,
        CALL_METADATA_KEY,
        replace(
            mark, live=live, held_seconds=mark.held_seconds + max(held_for, 0.0)
        ).as_metadata(),
    )


#: Earlier connections a resumed call reads back, newest kept. The voice is
#: given only the tail of them anyway.
MAX_RESUMED_TRANSCRIPTS = 10

#: The metadata key a connection's transcript is recorded under.
CALL_TRANSCRIPT_KEY = "call_transcript"


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


async def call_transcripts(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[str]:
    """What was said on the call's earlier connections, oldest first.

    Each connection records its transcript in the call's conversation as it
    ends, marked ``call_transcript``; a call resumed after a drop or a hang-up
    reads them back so the voice knows what it already said.
    """
    rows = await uow.session.execute(
        select(MessageModel.text)
        .where(
            MessageModel.conversation_id == call_id,
            MessageModel.kind == MessageKind.NOTIFICATION.value,
            MessageModel.message_metadata.has_key(CALL_TRANSCRIPT_KEY),
        )
        .order_by(MessageModel.created_at.desc(), MessageModel.sequence.desc())
        .limit(MAX_RESUMED_TRANSCRIPTS)
    )
    return [text for text in reversed(rows.scalars().all()) if text]


async def call_conversations(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[UUID]:
    """The call's own conversation and every sub-agent's at any depth, oldest
    first.

    By the mark each one carries rather than by parent: a sub-agent's own
    sub-agents are in the call too, and were missed after a reconnect when
    only direct children were read. The newest are kept when there are more.
    """
    pod_id = select(ConversationModel.pod_id).where(ConversationModel.id == call_id)
    rows = await uow.session.execute(
        select(ConversationModel.id)
        .where(
            ConversationModel.pod_id == pod_id.scalar_subquery(),
            or_(
                ConversationModel.id == call_id,
                ConversationModel.conversation_metadata[CALL_METADATA_KEY][
                    "call_id"
                ].astext
                == str(call_id),
            ),
        )
        .order_by(ConversationModel.created_at.desc())
        .limit(MAX_CALL_CONVERSATIONS)
    )
    return list(reversed(rows.scalars().all()))


async def call_working(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> bool:
    """Whether anyone in the call -- the coordinator or a sub-agent -- has a
    run going, read when the voice cannot have been told."""
    conversations = await call_conversations(uow, call_id)
    return bool(
        await uow.session.scalar(
            select(
                exists().where(
                    AgentRunModel.conversation_id.in_(conversations),
                    AgentRunModel.status.in_(
                        [
                            AgentRunStatus.RUNNING.value,
                            AgentRunStatus.STOP_REQUESTED.value,
                        ]
                    ),
                )
            )
        )
    )


#: Relays a resumed call reads back, at most.
MAX_MISSED_RELAYS = 20


async def missed_relays(
    uow: SqlAlchemyUnitOfWork, call_id: UUID
) -> list[tuple[str, str]]:
    """What the coordinator relayed to a voice nobody was connected to.

    Since the call's last transcript was written, which is when its last
    connection ended: ``(text, urgency)``, oldest first. A relay goes out over
    live events only, so one sent while the line was down was never heard.
    """
    last = await uow.session.scalar(
        select(MessageModel.created_at)
        .where(
            MessageModel.conversation_id == call_id,
            MessageModel.kind == MessageKind.NOTIFICATION.value,
            MessageModel.message_metadata.has_key(CALL_TRANSCRIPT_KEY),
        )
        .order_by(MessageModel.created_at.desc())
        .limit(1)
    )
    if last is None:
        return []
    rows = await uow.session.execute(
        select(MessageModel.tool_args)
        .where(
            MessageModel.conversation_id == call_id,
            MessageModel.kind == MessageKind.TOOL_CALL.value,
            MessageModel.tool_name == RELAY_TOOL_NAME,
            MessageModel.created_at > last,
        )
        .order_by(MessageModel.created_at)
        .limit(MAX_MISSED_RELAYS)
    )
    relays: list[tuple[str, str]] = []
    for args in rows.scalars():
        if isinstance(args, dict) and isinstance(args.get("text"), str):
            relays.append((args["text"], str(args.get("urgency") or "when_idle")))
    return relays


async def call_canvas(uow: SqlAlchemyUnitOfWork, call_id: UUID) -> list[CanvasItem]:
    """Everything shown on the call's canvas so far, in the order it was shown:
    since the conversation became a call, so a chat's own earlier displays,
    never on a call's screen, are not put there now."""
    conversations = await call_conversations(uow, call_id)
    mark = await held_call(uow, call_id)
    since = mark.since if mark is not None else None
    rows = (
        await uow.session.execute(
            select(MessageModel)
            .where(
                MessageModel.conversation_id.in_(conversations),
                MessageModel.tool_name.in_(CANVAS_TOOL_NAMES),
                MessageModel.kind.in_(
                    [MessageKind.TOOL_CALL.value, MessageKind.TOOL_RETURN.value]
                ),
                *([MessageModel.created_at >= since] if since is not None else []),
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
    "call_working",
    "held_call",
    "missed_relays",
    "open_call",
    "owned_call",
    "set_call_live",
]
