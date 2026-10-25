"""Which call a new conversation belongs to, inherited from its parent."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.agent.domain.calls import (
    CALL_METADATA_KEY,
    call_mark,
    worker_canvas_note,
)
from app.modules.agent.infrastructure.db.conversations import Conversations

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


async def call_of_parent(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> dict[str, object]:
    """The call mark a child inherits, as metadata, when its parent is in a call.

    So a sub-agent spawned from a live call draws on that call's canvas.
    """
    raw = await Conversations(uow).get_conversation_metadata_key(
        conversation_id, CALL_METADATA_KEY
    )
    mark = call_mark({CALL_METADATA_KEY: raw})
    if mark is None:
        return {}
    return {CALL_METADATA_KEY: mark.for_worker().as_metadata()}


__all__ = ["call_of_parent", "worker_canvas_note"]
