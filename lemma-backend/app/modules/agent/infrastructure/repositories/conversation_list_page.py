"""One page of the history list, most recently active first.

Split from `ConversationRepository` the way `conversation_opening_texts` was:
one read, one caller (`list_conversations`), and a keyset rule worth reading on
its own.
"""

from __future__ import annotations

from sqlalchemy import Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.entities import Conversation as ConversationEntity
from app.modules.agent.domain.value_objects import ConversationListCursor
from app.modules.agent.infrastructure.models import ConversationModel


async def page_by_activity(
    session: AsyncSession,
    stmt: Select[tuple[ConversationModel]],
    *,
    cursor: ConversationListCursor | None,
    limit: int,
) -> tuple[list[ConversationEntity], ConversationListCursor | None]:
    # `id` breaks ties so the order is total and the keyset never skips or
    # repeats a row. The row comparison, rather than two inequalities, is what
    # lets the `*_activity` indexes serve it as a single range.
    if cursor is not None:
        stmt = stmt.where(
            tuple_(ConversationModel.last_activity_at, ConversationModel.id)
            < tuple_(literal(cursor.last_activity_at), literal(cursor.id))
        )
    stmt = stmt.order_by(
        ConversationModel.last_activity_at.desc(), ConversationModel.id.desc()
    ).limit(limit + 1)
    result = await session.execute(stmt)
    rows = list(result.scalars())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    next_cursor = (
        ConversationListCursor(
            last_activity_at=rows[-1].last_activity_at, id=rows[-1].id
        )
        if has_more and rows
        else None
    )
    return [row.to_entity() for row in rows], next_cursor
