"""A contact's channel's cases read back, newest first, each with what
happened in it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.cases import MAX_CASE_EVENTS, Case, CaseEvent
from app.modules.channels.domain.entities import ContactStatus
from app.modules.channels.infrastructure.models import (
    ChannelCaseEventModel,
    ChannelCaseModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES


async def cases_of(
    session: AsyncSession,
    channel_id: UUID,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[Case]:
    """A page of the channel's cases, the most recently opened first --
    older than ``before`` (an ``(opened_at, id)``) when given."""
    case = ChannelCaseModel
    statement = select(case).where(case.channel_id == channel_id)
    if before is not None:
        statement = statement.where(
            tuple_(case.opened_at, case.id) < tuple_(before[0], before[1])
        )
    rows = list(
        await session.scalars(
            statement.order_by(case.opened_at.desc(), case.id.desc()).limit(
                max(1, min(limit, MAX_LINES))
            )
        )
    )
    events = await _events_of(session, [row.id for row in rows])
    return [_case(row, events.get(row.id, [])) for row in rows]


async def _events_of(
    session: AsyncSession, case_ids: list[UUID]
) -> dict[UUID, list[CaseEvent]]:
    """Each case's events, oldest first: the newest ``MAX_CASE_EVENTS`` of a
    long one, read along ``(case_id, id)``."""
    if not case_ids:
        return {}
    event = ChannelCaseEventModel
    newest = (
        select(
            event.case_id,
            event.id,
            event.at,
            event.kind,
            event.actor_user_id,
            event.value,
            func.row_number()
            .over(partition_by=event.case_id, order_by=event.id.desc())
            .label("n"),
        )
        .where(event.case_id.in_(case_ids))
        .subquery()
    )
    rows = await session.execute(
        select(
            newest.c.case_id,
            newest.c.at,
            newest.c.kind,
            newest.c.actor_user_id,
            newest.c.value,
        )
        .where(newest.c.n <= MAX_CASE_EVENTS)
        .order_by(newest.c.case_id, newest.c.id)
    )
    found: dict[UUID, list[CaseEvent]] = {}
    for case_id, at, kind, actor, value in rows.tuples():
        found.setdefault(case_id, []).append(
            CaseEvent(at=at, kind=kind, actor_user_id=actor, value=value or {})
        )
    return found


def _case(row: ChannelCaseModel, events: list[CaseEvent]) -> Case:
    return Case(
        id=row.id,
        channel_id=row.channel_id,
        status=ContactStatus(row.status),
        opened_at=row.opened_at,
        opened_seq=row.opened_seq,
        assignee_user_id=row.assignee_user_id,
        assigned_at=row.assigned_at,
        attention_at=row.attention_at,
        attention_reason=row.attention_reason,
        first_response_at=row.first_response_at,
        first_response_by_user_id=row.first_response_by_user_id,
        closed_at=row.closed_at,
        closed_by_user_id=row.closed_by_user_id,
        events=tuple(events),
    )


__all__ = ["cases_of"]
