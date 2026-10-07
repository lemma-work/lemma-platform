"""One page of the history list, most recently active first.

Split from `ConversationRepository` the way `conversation_opening_texts` was:
one read, one caller (`list_conversations`), and a keyset rule worth reading on
its own. The filters live here too, beside the order they have to agree with.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, and_, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.infrastructure.db.sql_text import escape_like
from app.modules.agent.domain.entities import Conversation as ConversationEntity
from app.modules.agent.domain.value_objects import (
    ConversationAgentScope,
    ConversationAgentSelection,
    ConversationListCursor,
    ConversationStatus,
    ConversationType,
    JsonObject,
)
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.repository_status import (
    conversation_status_values_for_db,
)


def readable_by(*, user_id: UUID, pod_id: UUID) -> ColumnElement[bool]:
    """The conversations one person may read in one pod: their own.

    The rule ``validate_conversation_access`` applies to a single conversation,
    as a predicate, so that anything reading many -- this list, the scorecard's
    counts -- reads the same set and cannot drift into someone else's.
    """
    return and_(
        ConversationModel.user_id == user_id,
        ConversationModel.pod_id == pod_id,
    )


def list_statement(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_selection: ConversationAgentSelection[UUID],
    status: ConversationStatus | None,
    conversation_type: ConversationType | None,
    metadata_filters: JsonObject | None,
    parent_id: UUID | None,
    archived: bool,
    search: str | None,
) -> Select[tuple[ConversationModel]]:
    # One list or the other, never both: the archive is a place you go, not
    # a tail on the end of the history. Equality rather than "not archived"
    # so the same query serves both without a second code path.
    stmt = select(ConversationModel).where(
        readable_by(user_id=user_id, pod_id=pod_id),
        ConversationModel.is_archived.is_(archived),
    )
    # Default: root conversations only. With parent_id: that conversation's
    # children (sub-agent conversations).
    if parent_id is None:
        stmt = stmt.where(ConversationModel.parent_id.is_(None))
    else:
        stmt = stmt.where(ConversationModel.parent_id == parent_id)
    if agent_selection.scope is not ConversationAgentScope.ALL:
        # The assistant is named by the pod's own id, and a conversation
        # written before it had a row still names it by naming nobody. The
        # COALESCE covers both, and `ix_agent_conv_user_pod_agent_roots_activity`
        # is defined on exactly this expression -- change one and the index
        # stops being used, silently.
        selected_agent_id = pod_id
        if agent_selection.scope is ConversationAgentScope.NAMED:
            selected_agent_id = agent_selection.named_value
        agent_scope_id = func.coalesce(
            ConversationModel.agent_id, ConversationModel.pod_id
        )
        stmt = stmt.where(agent_scope_id == selected_agent_id)
    if status is not None:
        stmt = stmt.where(
            ConversationModel.status.in_(conversation_status_values_for_db(status))
        )
    if conversation_type is not None:
        stmt = stmt.where(
            ConversationModel.conversation_type == conversation_type.value
        )
    if metadata_filters:
        stmt = stmt.where(
            ConversationModel.conversation_metadata.op("@>")(metadata_filters)
        )
    if search:
        # A filter inside the index range, not a search index: one person's
        # conversations in one pod is a set the planner walks in order anyway.
        # Escaped, because a `%` somebody types is a character, not a wildcard.
        stmt = stmt.where(
            ConversationModel.title.ilike(f"%{escape_like(search)}%", escape="!")
        )
    return stmt


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
            < tuple_(
                # Typed from the columns: an untyped datetime literal binds as
                # a naive TIMESTAMP, which asyncpg refuses an aware value for.
                literal(
                    cursor.last_activity_at, ConversationModel.last_activity_at.type
                ),
                literal(cursor.id, ConversationModel.id.type),
            )
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


async def cursor_after(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
) -> ConversationListCursor | None:
    """The position just after one of this person's conversations.

    For a page token from before the list was ordered by activity, which was
    a bare conversation id. Scoped to the caller's own rows in the pod, so a
    token cannot be used to learn when anybody else's conversation was active.
    """
    result = await session.execute(
        select(ConversationModel.last_activity_at).where(
            ConversationModel.id == conversation_id,
            ConversationModel.user_id == user_id,
            ConversationModel.pod_id == pod_id,
        )
    )
    last_activity_at = result.scalar_one_or_none()
    if last_activity_at is None:
        return None
    return ConversationListCursor(last_activity_at=last_activity_at, id=conversation_id)
