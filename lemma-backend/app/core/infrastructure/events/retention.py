"""Bounded retention for durable event delivery records."""

from __future__ import annotations

import time
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
    """Drain each retention category, bounded by a wall-clock budget.

    This used to delete exactly one batch per category per run. With the
    default 1,000-row batch and an hourly cron that is a ceiling of 24,000
    rows a day, which any install producing events faster than that outruns --
    the table then grows without bound no matter what the retention window
    says, and the pruner never catches up. That is how the outbox reached
    several hundred thousand rows.

    So each category now deletes repeatedly until a short batch says it is
    drained. The budget keeps one sweep from running into the next: it is
    checked between batches, so a run stops cleanly at a batch boundary and
    the next cron tick resumes where it left off. Nothing is lost by stopping
    early -- the cutoff is recomputed from ``now`` on the next run, and rows
    past it stay eligible.
    """
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
    budget = event_transport_settings.event_retention_run_budget_seconds
    started = time.monotonic()

    def budget_spent() -> bool:
        return budget > 0 and (time.monotonic() - started) >= budget

    deleted: dict[str, int] = {}
    for name, model, filters in categories:
        removed = 0
        while True:
            # One batch per transaction, as before. Holding a single
            # transaction open across the whole drain would pin a connection
            # and keep every deleted row's tuple alive for the duration.
            async with session_maker() as session, session.begin():
                batch = await _delete_batch(
                    session,
                    model,
                    *filters,
                    batch_size=batch_size,
                )
            removed += batch
            # A short batch means the category is drained. Anything skipped by
            # SKIP LOCKED is being deleted by a concurrent sweep, or is leased
            # by the dispatcher and no longer matches the cutoff anyway.
            if batch < batch_size or budget == 0 or budget_spent():
                break
        deleted[name] = removed
    return deleted
