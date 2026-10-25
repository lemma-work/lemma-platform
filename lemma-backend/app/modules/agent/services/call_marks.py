"""Which call a new conversation belongs to, inherited from its parent."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.agent.domain.calls import CallMark, worker_canvas_note
from app.modules.agent.infrastructure.db.conversations import Conversations

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


async def call_of_parent(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> CallMark | None:
    """The call mark a child inherits when its parent is in a call.

    So a sub-agent spawned from a live call draws on that call's canvas.
    """
    mark = await Conversations(uow).get_call(conversation_id)
    return mark.for_worker() if mark is not None else None


__all__ = ["call_of_parent", "worker_canvas_note"]
