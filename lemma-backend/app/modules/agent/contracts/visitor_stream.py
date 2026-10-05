"""What a person outside the pod may watch of a conversation, as it happens.

The member's stream carries everything a run publishes: the model's thinking,
tool calls and their results, a private note and the run that answers it. A
visitor on a web page may see none of that. This is the same rule
``visible_messages`` applies to history, applied to the live frames: the answer's
text as it is written, a "typing" signal while the run works on anything else,
the finished message, and "done".

Every frame is translated rather than filtered, so a field added to the
member's frames later reaches no visitor by accident.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.infrastructure.models.conversation import AgentRunModel
from app.modules.agent.services.realtime import conversation_channel

__all__ = ["VisitorFrame", "visitor_frame", "visitor_frames"]

#: One frame as the visitor's page receives it.
type VisitorFrame = dict[str, object]

TYPING: VisitorFrame = {"type": "typing"}
DONE: VisitorFrame = {"type": "done"}
RESET: VisitorFrame = {"type": "reset"}

_TERMINAL = frozenset({"completed", "error", "stopped"})
#: Token lanes that are the run working rather than answering.
_WORKING_TOKENS = frozenset({"thinking", "tool"})
_STREAM_RESET = "stream_reset"

#: How long a quiet stream waits before sending a blank line, so proxies keep it.
KEEPALIVE_SECONDS = 15.0


def visitor_frame(payload: Mapping[str, object]) -> VisitorFrame | None:
    """The frame a visitor sees for one member frame, or ``None`` for nothing.

    Pure: whether the frame's run is private is decided before this is asked.
    """
    kind = payload.get("type")
    if kind == "token":
        return _token_frame(payload)
    if kind == "status":
        return TYPING
    if kind == "message":
        data = payload.get("data")
        return _message_frame(data) if isinstance(data, Mapping) else None
    if kind in _TERMINAL:
        return DONE
    return None


def _token_frame(payload: Mapping[str, object]) -> VisitorFrame | None:
    lane = str(payload.get("kind") or "text")
    if lane == _STREAM_RESET:
        return RESET
    if lane in _WORKING_TOKENS:
        return TYPING
    if lane != "text":
        return None
    text = payload.get("data")
    if not isinstance(text, str) or not text:
        return None
    return {"type": "delta", "text": text}


def _message_frame(message: Mapping[str, object]) -> VisitorFrame | None:
    if message.get("role") != "assistant":
        return None
    metadata = message.get("metadata")
    if is_private_note(metadata if isinstance(metadata, Mapping) else None):
        return None
    if message.get("kind") == MessageKind.TOOL_CALL.value:
        return TYPING
    text = message.get("text")
    if message.get("kind") != MessageKind.TEXT.value or not isinstance(text, str):
        return None
    if not text.strip():
        return None
    return {"type": "message", "text": text, "sequence": message.get("sequence")}


def _private_run_lookup(
    uow_factory: UnitOfWorkFactory,
) -> Callable[[str], Awaitable[bool]]:
    """Whether a run is private, asked once per run on a short unit of work."""
    known: dict[str, bool] = {}

    async def is_private(run_id: str) -> bool:
        if run_id not in known:
            try:
                parsed = UUID(run_id)
            except ValueError:
                known[run_id] = True
                return True
            async with uow_factory() as uow:
                metadata = await uow.session.scalar(
                    select(AgentRunModel.run_metadata).where(AgentRunModel.id == parsed)
                )
            known[run_id] = run_is_private(metadata)
        return known[run_id]

    return is_private


async def visitor_frames(
    conversation_id: UUID,
    *,
    uow_factory: UnitOfWorkFactory,
    max_seconds: float,
) -> AsyncIterator[VisitorFrame | None]:
    """The visitor's frames for one conversation, for at most ``max_seconds``.

    ``None`` is a keepalive. A frame from a private run is dropped whole --
    including its "typing" -- so a member's note leaves no trace on the page.
    """
    is_private = _private_run_lookup(uow_factory)
    deadline = time.monotonic() + max_seconds
    service = await get_channel_service()
    async with service.subscribe([conversation_channel(conversation_id)]) as source:
        pending: asyncio.Task[str | bytes] | None = None
        try:
            while (left := deadline - time.monotonic()) > 0:
                if pending is None:
                    pending = asyncio.ensure_future(anext(source))
                done, _ = await asyncio.wait(
                    {pending}, timeout=min(KEEPALIVE_SECONDS, left)
                )
                if not done:
                    yield None
                    continue
                try:
                    raw = pending.result()
                except StopAsyncIteration:
                    return
                finally:
                    pending = None
                frame = await _translate(raw, is_private)
                if frame is not None:
                    yield frame
        finally:
            if pending is not None:
                pending.cancel()


async def _translate(
    raw: str | bytes, is_private: Callable[[str], Awaitable[bool]]
) -> VisitorFrame | None:
    payload = _parsed(raw)
    if payload is None:
        return None
    run_id = payload.get("agent_run_id")
    if isinstance(run_id, str) and await is_private(run_id):
        return None
    return visitor_frame(payload)


def _parsed(raw: str | bytes) -> Mapping[str, object] | None:
    """One published frame, decoded. Frames are a few hundred bytes at most."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, Mapping) else None
