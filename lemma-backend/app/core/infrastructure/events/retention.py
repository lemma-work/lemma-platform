"""Bounded retention for durable event delivery records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.inbox import InboxStatus
from app.core.infrastructure.events.models import DomainEventInbox, DomainEventOutbox


async def _delete_batch(
    session: AsyncSession,
    model,
    *filters,
    batch_size: int,
) -> int:
    claimed = (
        select(model.id)
        .where(*filters)
        .order_by(model.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .cte("retention_batch")
    )
    result = await session.execute(
        delete(model).where(model.id.in_(select(claimed.c.id)))
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_event_delivery_records(
    session_maker: Callable[[], AsyncSession],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete at most one configured batch from each retention category."""
    now = now or datetime.now(timezone.utc)
    completed_cutoff = now - timedelta(
        days=event_transport_settings.event_completed_retention_days
    )
    dead_cutoff = now - timedelta(
        days=event_transport_settings.event_dead_letter_retention_days
    )
    batch_size = event_transport_settings.event_retention_batch_size
    categories: tuple[tuple[str, Any, tuple[Any, ...]], ...] = (
        (
            "outbox_published",
            DomainEventOutbox,
            (
                DomainEventOutbox.published_at.is_not(None),
                DomainEventOutbox.published_at < completed_cutoff,
            ),
        ),
        (
            "outbox_dead_letter",
            DomainEventOutbox,
            (
                DomainEventOutbox.dead_lettered_at.is_not(None),
                DomainEventOutbox.dead_lettered_at < dead_cutoff,
            ),
        ),
        (
            "inbox_completed",
            DomainEventInbox,
            (
                DomainEventInbox.status.in_(
                    (InboxStatus.COMPLETED.value, InboxStatus.TERMINAL.value)
                ),
                DomainEventInbox.completed_at < completed_cutoff,
            ),
        ),
        (
            "inbox_dead_letter",
            DomainEventInbox,
            (
                DomainEventInbox.status == InboxStatus.DEAD_LETTER.value,
                DomainEventInbox.dead_lettered_at < dead_cutoff,
            ),
        ),
    )
    deleted: dict[str, int] = {}
    for name, model, filters in categories:
        async with session_maker() as session, session.begin():
            deleted[name] = await _delete_batch(
                session,
                model,
                *filters,
                batch_size=batch_size,
            )
    return deleted
