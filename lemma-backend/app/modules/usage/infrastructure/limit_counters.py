"""Admitting work against a spend window, and settling up afterwards.

The counters are the transactional half of a limit. Spend itself is durable in
``usage_records``; a counter exists so that admission can be decided *now*,
against work that has started and not yet been priced, without every concurrent
caller re-reading the ledger and reaching the same optimistic conclusion.

Three properties hold everything together, and each is load-bearing:

**Reserve is atomic.** ``INSERT ... ON CONFLICT DO NOTHING`` then a locked
``SELECT`` is the create-or-lock idiom: whoever loses the insert race still
finds and locks the winner's row, so two concurrent reservers serialize rather
than both reading a stale ``reserved_usd``.

**Locks are taken in one order.** Every statement here that locks more than one
counter sorts first -- by scope on the way in, by id on the way out. Two callers
touching the same pair of counters in opposite orders is a deadlock, and the
sort is the only thing preventing it.

**Nothing is decided until every scope is locked.** The cap check runs after the
loop, so a reservation that the tightest window refuses leaves no increment
behind on the windows that had headroom.

Free functions over a session rather than repository methods, matching
``usage_limit_reads`` next door: none of this reads or writes a usage record,
and the repository is a file whose length is ratcheted.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.domain.entities import UsageLimitCounterScope
from app.modules.usage.infrastructure.models import UsageLimitCounter


def _identifies(scope_organization_id: UUID | None, scope_user_id: UUID | None):
    """The predicate matching one counter's key, nulls included.

    ``organization_id`` and ``user_id`` are each nullable and each meaningful
    when null -- an ``org_month`` row has no user, and a globally scoped user
    row has no organization. ``IS NULL`` rather than ``= NULL`` is therefore not
    a nicety; the unique index carries ``NULLS NOT DISTINCT`` for the same
    reason.
    """
    return [
        (
            UsageLimitCounter.organization_id.is_(None)
            if scope_organization_id is None
            else UsageLimitCounter.organization_id == scope_organization_id
        ),
        (
            UsageLimitCounter.user_id.is_(None)
            if scope_user_id is None
            else UsageLimitCounter.user_id == scope_user_id
        ),
    ]


async def reserve_limit_scopes(
    session: AsyncSession,
    *,
    scopes: list[UsageLimitCounterScope],
    amount_usd: float,
) -> list[UUID] | None:
    """Atomically admit and reserve every applicable limit scope.

    ``None`` means at least one locked scope would exceed its cap. An empty list
    means no limit applies. The caller owns the surrounding unit of work, so all
    increments commit or roll back together.
    """
    if not scopes:
        return []

    ordered = sorted(
        scopes,
        key=lambda item: (
            item.window_kind,
            str(item.organization_id or ""),
            str(item.user_id or ""),
            item.window_start.isoformat(),
        ),
    )
    counters: list[UsageLimitCounter] = []
    for scope in ordered:
        await session.execute(
            insert(UsageLimitCounter)
            .values(
                organization_id=scope.organization_id,
                user_id=scope.user_id,
                window_kind=scope.window_kind,
                window_start=scope.window_start,
                window_end=scope.window_end,
                used_usd=scope.initial_used_usd,
                reserved_usd=0.0,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    UsageLimitCounter.organization_id,
                    UsageLimitCounter.user_id,
                    UsageLimitCounter.window_kind,
                    UsageLimitCounter.window_start,
                )
            )
        )
        conditions = [
            UsageLimitCounter.window_kind == scope.window_kind,
            UsageLimitCounter.window_start == scope.window_start,
            *_identifies(scope.organization_id, scope.user_id),
        ]
        counter = (
            await session.scalars(
                select(UsageLimitCounter).where(and_(*conditions)).with_for_update()
            )
        ).one()
        # Synchronize pre-migration/history spend without ever lowering the
        # transactionally maintained counter.
        counter.used_usd = max(float(counter.used_usd or 0.0), scope.initial_used_usd)
        counters.append(counter)

    if any(
        float(counter.used_usd or 0.0) + float(counter.reserved_usd or 0.0) + amount_usd
        > scope.limit_usd
        for counter, scope in zip(counters, ordered, strict=True)
    ):
        return None

    for counter in counters:
        counter.reserved_usd = float(counter.reserved_usd or 0.0) + amount_usd
    await session.flush()
    return [counter.id for counter in counters]


async def release_reservation(
    session: AsyncSession,
    *,
    counter_ids: list[UUID],
    amount_usd: float,
) -> None:
    """Hand back a hold nothing is going to settle."""
    await _adjust(
        session,
        counter_ids=counter_ids,
        reserved_delta=-amount_usd,
        used_delta=0.0,
    )


async def consume_reservation(
    session: AsyncSession,
    *,
    counter_ids: list[UUID],
    reserved_usd: float,
    actual_usd: float,
) -> None:
    """Turn a hold into spend: drop the reservation, add what it really cost."""
    await _adjust(
        session,
        counter_ids=counter_ids,
        reserved_delta=-reserved_usd,
        used_delta=actual_usd,
    )


async def _adjust(
    session: AsyncSession,
    *,
    counter_ids: list[UUID],
    reserved_delta: float,
    used_delta: float,
) -> None:
    """Move both figures on a set of counters, under one ordered lock.

    ``ORDER BY id`` on both settling paths, not just one: a release and a
    consume that overlap on two counters and take them in opposite orders
    deadlock, and the two paths used to disagree.

    Both figures are floored at zero. A hold released twice -- the worker and
    the orphan reconciler reaching for the same run -- would otherwise drive
    ``reserved_usd`` negative and hand back allowance that was only ever taken
    once. The floor is a backstop; the reservation handle is claimed under a row
    lock so the second release finds nothing to give back.
    """
    if not counter_ids:
        return
    result = await session.execute(
        select(UsageLimitCounter)
        .where(UsageLimitCounter.id.in_(counter_ids))
        .order_by(UsageLimitCounter.id)
        .with_for_update()
    )
    for counter in result.scalars().all():
        counter.reserved_usd = max(
            0.0, float(counter.reserved_usd or 0.0) + reserved_delta
        )
        counter.used_usd = max(0.0, float(counter.used_usd or 0.0) + used_delta)
    await session.flush()
