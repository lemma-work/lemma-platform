"""Keyset pages of owners with excess builds or incomplete storage deletion.

The pending-deletion branch is independent of retained version count and age.
An owner leaves the candidate set only after its cleanup has been acknowledged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, func, or_, select, union
from sqlalchemy.orm import InstrumentedAttribute

from app.core.retention import RetentionPolicy


def owners_with_prunable_versions(
    *,
    owner_column: InstrumentedAttribute[UUID],
    created_at_column: InstrumentedAttribute[datetime],
    pruned_at_column: InstrumentedAttribute[datetime | None],
    purged_at_column: InstrumentedAttribute[datetime | None],
    policy: RetentionPolicy,
    now: datetime,
    after: UUID | None = None,
    limit: int,
) -> Select[tuple[UUID]]:
    """One page of owners that could have a prunable version, id-ordered.

    Mirrors ``could_have_prunable`` exactly -- ``k > keep_last AND (k > max_keep
    OR oldest < cutoff)`` over the unpruned rows -- and inherits its one-sided
    guarantee: it may return an owner the rule then spares (the live version and
    an in-flight run are invisible from here), never the reverse.
    """
    cutoff = now - timedelta(days=policy.keep_days)
    statement = (
        select(owner_column)
        .where(pruned_at_column.is_(None))
        .group_by(owner_column)
        .having(func.count() > policy.keep_last)
        .having(
            or_(
                func.count() > policy.max_keep,
                func.min(created_at_column) < cutoff,
            )
        )
    )
    pending = select(owner_column).where(
        pruned_at_column.is_not(None), purged_at_column.is_(None)
    )
    if after is not None:
        statement = statement.where(owner_column > after)
        pending = pending.where(owner_column > after)
    candidates = union(statement, pending).subquery()
    return select(candidates.c[0]).order_by(candidates.c[0]).limit(limit)
