"""What a claimed one-shot timer looks like, wherever it came from.

Timers live in the modules that own them -- workflow waits in workflow, snoozes
in agent -- because a module owning a table should own the query against it. The
poller collects them through injected claimers rather than importing those
models.

These shared pieces sit in core rather than in `schedule` for the same reason:
`agent` may not import `schedule`, and a claimer that has to reach across a
module boundary to describe its own return type is a boundary in the wrong
place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_

#: How long a claim holds a timer before another replica may retry it. Long
#: enough that an ordinary dispatch finishes inside it, short enough that a
#: replica dying mid-fire delays the wake by seconds rather than minutes.
FIRE_LEASE_SECONDS = 60

DEFAULT_TIMER_CLAIM_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ClaimedTimer:
    """One timer this replica owns for the length of its lease."""

    timer_id: UUID
    user_id: UUID | None
    fire_at: datetime
    payload: dict


def lease_is_free(column, now: datetime):
    """A row is claimable when nobody holds it, or the holder's lease lapsed."""
    return or_(column.is_(None), column <= now)


def lease_expiry(now: datetime) -> datetime:
    return now + timedelta(seconds=FIRE_LEASE_SECONDS)
