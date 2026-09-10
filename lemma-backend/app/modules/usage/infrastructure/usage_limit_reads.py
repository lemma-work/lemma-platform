"""The reads behind ``get_usage_limits``, batched.

Answering "may this call proceed" needed six serial aggregates: three spend
sums over ``usage_records`` — which migration 0018 records as the highest
insert-rate table in the system — and three reserved-counter reads. They ran
strictly in sequence on the request path.

Neither collapse changes what is counted; both change how many times the
database is asked.

Not cached, deliberately. These numbers gate spending, and a cached limit lets
a caller overspend by the TTL's worth before anything notices. Not gathered
either: six concurrent checkouts from a ``pool_size=10``, ``max_overflow=0``
pool is a worse trade than three sequential statements.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import UsageLimitCounter
from app.modules.usage.infrastructure.models import UsageRecord as UsageRecordModel


async def system_cost_by_window(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    user_id: UUID | None,
    window_starts: Mapping[str, datetime],
    end: datetime,
    exclude_organization_ids: Sequence[UUID] = (),
) -> dict[str, float]:
    """System spend for several windows over the same rows, in one scan.

    The user's weekly and monthly spend differ only in where the window
    begins, so asking separately scanned the same rows twice. One scan from
    the earliest window start, with a ``FILTER`` per window, answers both.

    Deliberately not extended to cover the *organization* total as well. That
    one carries a different predicate — a whole org, rather than one user in
    it — so folding it in would force a disjunction the composite indexes
    cannot serve and widen the scan to every user in the organization. Two
    narrow statements beat one wide one.
    """
    if not window_starts:
        return {}
    stmt = select(
        *(
            func.coalesce(
                func.sum(recorded_cost()).filter(
                    UsageRecordModel.occurred_at >= window_start
                ),
                Decimal(0),
            ).label(name)
            for name, window_start in window_starts.items()
        )
    ).where(
        UsageRecordModel.occurred_at >= min(window_starts.values()),
        UsageRecordModel.occurred_at <= end,
    )
    stmt = stmt.where(
        UsageRecordModel.profile_scope == "SYSTEM", recorded_cost().is_not(None)
    )
    if organization_id is not None:
        stmt = stmt.where(UsageRecordModel.organization_id == organization_id)
    if user_id is not None:
        stmt = stmt.where(UsageRecordModel.user_id == user_id)
    if exclude_organization_ids:
        stmt = stmt.where(
            or_(
                UsageRecordModel.organization_id.is_(None),
                UsageRecordModel.organization_id.notin_(
                    tuple(exclude_organization_ids)
                ),
            )
        )
    row = (await session.execute(stmt)).one()
    return {
        name: float(value or 0.0)
        for name, value in zip(window_starts, row, strict=True)
    }


async def reserved_costs(
    session: AsyncSession,
    *,
    scopes: Sequence[tuple[UUID | None, UUID | None, str, datetime]],
) -> dict[str, float]:
    """Reserved spend for several scopes, keyed by window kind.

    Each scope is an exact ``(organization_id, user_id, window_kind,
    window_start)`` tuple against the unique index on those four columns, so
    one statement with an OR of the tuples is three index probes rather than
    three round trips. Keyed by ``window_kind`` because the three callers use
    three distinct kinds; a scope with no counter row reads as 0.0, which is
    what an absent counter means and what the caller indexes into directly.
    """
    if not scopes:
        return {}
    clauses = [
        and_(
            UsageLimitCounter.window_kind == window_kind,
            UsageLimitCounter.window_start == window_start,
            UsageLimitCounter.organization_id == organization_id
            if organization_id is not None
            else UsageLimitCounter.organization_id.is_(None),
            UsageLimitCounter.user_id == user_id
            if user_id is not None
            else UsageLimitCounter.user_id.is_(None),
        )
        for organization_id, user_id, window_kind, window_start in scopes
    ]
    stmt = (
        select(
            UsageLimitCounter.window_kind,
            func.coalesce(func.sum(UsageLimitCounter.reserved_usd), Decimal(0)),
        )
        .where(or_(*clauses))
        .group_by(UsageLimitCounter.window_kind)
    )
    rows = (await session.execute(stmt)).all()
    totals = {window_kind: float(total or 0.0) for window_kind, total in rows}
    return {
        window_kind: totals.get(window_kind, 0.0) for _, _, window_kind, _ in scopes
    }
