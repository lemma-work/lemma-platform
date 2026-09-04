"""Where a spend window starts, where it ends, and what is left in it.

Calendar arithmetic and one dict shape, split out of the service because none of
it touches storage or the limit port -- and because the service is a long file
whose length is ratcheted.

Windows are UTC calendar periods: a month starts on the 1st, a week on Monday.
Deliberately *not* the subscription's billing period, which can start on any day:
the counters are keyed by ``window_start``, so a per-customer period would mean
every organization getting its own key space for the same month. The cost of that
choice is that an allowance resets on the 1st rather than on the anniversary of
the subscription, which is what the user-facing copy has to say.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def week_start(now: datetime) -> datetime:
    return (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def next_month_start(now: datetime) -> datetime:
    if now.month == 12:
        return now.replace(
            year=now.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return now.replace(
        month=now.month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def limit_scope(
    *,
    limit_usd: float | None,
    used_usd: float,
    reserved_usd: float,
    reset_at: datetime,
    window_start_at: datetime,
    scope: str,
    counter_organization_id: UUID | None,
) -> dict[str, object]:
    """One window's state, as the limits API and the reservation path read it."""
    consumed = used_usd + reserved_usd
    remaining = None if limit_usd is None else max(0.0, limit_usd - consumed)
    return {
        "limit_usd": limit_usd,
        "scope": scope,
        "used_usd": used_usd,
        "reserved_usd": reserved_usd,
        "remaining_usd": remaining,
        "allowed": limit_usd is None or consumed < limit_usd,
        "reset_at": reset_at,
        "window_start": window_start_at,
        "counter_organization_id": counter_organization_id,
    }


def counter_scopes(
    *,
    organization_id: UUID | None,
    user_limit_organization_id: UUID | None,
    user_id: UUID,
    now: datetime,
) -> list[tuple[UUID | None, UUID | None, str, datetime]]:
    """The ``(org, user, window_kind, window_start)`` keys that apply right now.

    One place, because the reserve path, the limits read and the orphan sweep all
    have to address exactly the same counter rows, and a fourth hand-rolled copy
    of this tuple list is how they would stop agreeing.
    """
    scopes: list[tuple[UUID | None, UUID | None, str, datetime]] = [
        (user_limit_organization_id, user_id, "user_week", week_start(now)),
        (user_limit_organization_id, user_id, "user_month", month_start(now)),
    ]
    if organization_id is not None:
        scopes.append((organization_id, None, "org_month", month_start(now)))
    return scopes
