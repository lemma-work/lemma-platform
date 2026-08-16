"""Which owners a retention sweep should look at.

Both build-retention sweeps used to take ``ORDER BY id LIMIT 200`` with no
cursor and no filter, so every tick examined the same lowest-id rows forever and
the tail -- an app or function that STOPPED being deployed, which is the only
case the cron exists for -- was never reached.

The fix is not a cursor column. Unlike a sweep over rows that stay eligible
forever, this candidate set DRAINS: ``select_prunable`` never returns a row whose
``pruned_at`` is set, and the sweep stamps it in the same unit of work, so a
pruned version leaves the set permanently. What was missing is a filter precise
enough that the batch bound stops deciding *which* owners get swept and only
decides how many per round trip -- and a loop that keeps going until the page
comes back short.

The filter is :func:`app.core.retention.could_have_prunable` pushed into SQL.
Keyset, not OFFSET: rows leave the set as the sweep prunes them, so an offset
would step over owners it never looked at.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, func, or_, select

from app.core.retention import RetentionPolicy


def owners_with_prunable_versions(
    *,
    owner_column,
    created_at_column,
    pruned_at_column,
    policy: RetentionPolicy,
    now: datetime,
    after: UUID | None = None,
    limit: int,
) -> Select:
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
        .order_by(owner_column)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(owner_column > after)
    return statement
