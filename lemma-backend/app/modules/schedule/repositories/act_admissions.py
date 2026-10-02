"""Admitting a triaged event to act, at most `act_per_hour` a window, exactly once."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.modules.schedule.infrastructure.models.act_admission import (
    ScheduleActAdmission,
)


async def admit_act(
    session: AsyncSession,
    *,
    schedule_id: UUID,
    source_event_id: str,
    ceiling: int,
    window: timedelta,
    now: datetime,
) -> bool:
    """Whether this event may act now; if so, it holds one of the window's places.

    A transaction-scoped advisory lock on the schedule serialises admissions,
    so concurrent events cannot all read the same count and all pass. An event
    admitted before -- a redelivery, a retry after the fire failed to publish
    -- is admitted again without taking a second place. Admissions that have
    left the window are dropped as the count is read.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _lock_key(schedule_id)}
    )
    mark_transaction_scoped_lock(session)
    already = await session.scalar(
        select(ScheduleActAdmission.id).where(
            ScheduleActAdmission.schedule_id == schedule_id,
            ScheduleActAdmission.source_event_id == source_event_id,
        )
    )
    if already is not None:
        return True
    since = now - window
    await session.execute(
        delete(ScheduleActAdmission).where(
            ScheduleActAdmission.schedule_id == schedule_id,
            ScheduleActAdmission.created_at < since,
        )
    )
    admitted = await session.scalar(
        select(func.count())
        .select_from(ScheduleActAdmission)
        .where(ScheduleActAdmission.schedule_id == schedule_id)
    )
    if int(admitted or 0) >= ceiling:
        return False
    session.add(
        ScheduleActAdmission(
            schedule_id=schedule_id, source_event_id=source_event_id, created_at=now
        )
    )
    await session.flush()
    return True


def _lock_key(schedule_id: UUID) -> int:
    digest = hashlib.blake2b(
        str(schedule_id).encode(), digest_size=8, person=b"lemma-act-admit"
    ).digest()
    return int.from_bytes(digest, "big", signed=True)
