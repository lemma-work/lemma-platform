"""``lemma.active_users{window}``: distinct users with model usage lately.

There is no last-seen column on users and no sessions table, so activity is
read from ``usage_records``: a person who sent a message, ran an agent or had
one run for them caused a model call, and every model call writes a row with
its ``user_id``. That makes this "users the product did work for", which is
the useful definition for capacity and engagement, and misses only people who
browsed without asking for anything.

Cheap because the primary key is a UUIDv7: its leading 48 bits are the insert
time in milliseconds, so "since T" is a range on the primary-key index rather
than a scan, and no new index is paid on the table with the highest insert
rate in the system. One query covers all three windows, and it runs every
``_EVERY`` gauge ticks rather than every tick.
"""

from __future__ import annotations

import time
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work.gauges import register_gauge_sampler
from app.core.observability import product_metrics
from app.core.observability.product_metrics import ACTIVE_USER_WINDOWS

#: With the default 15s gauge interval, once every five minutes.
_EVERY = 20

_ACTIVE_USERS = text(
    """
    SELECT count(DISTINCT user_id) FILTER (WHERE id >= :since_5m)  AS w5m,
           count(DISTINCT user_id) FILTER (WHERE id >= :since_1h)  AS w1h,
           count(DISTINCT user_id)                                 AS w24h
      FROM usage_records
     WHERE id >= :since_24h
    """
)


def uuid7_floor(epoch_seconds: float) -> UUID:
    """The smallest UUIDv7 minted at or after ``epoch_seconds``."""
    millis = int(epoch_seconds * 1000)
    return UUID(int=(millis << 80) | (0x7 << 76) | (0b10 << 62))


async def sample_active_users(session: AsyncSession) -> None:
    now = time.time()
    row = (
        await session.execute(
            _ACTIVE_USERS,
            {
                f"since_{window}": uuid7_floor(now - seconds)
                for window, seconds in ACTIVE_USER_WINDOWS.items()
            },
        )
    ).one()
    product_metrics.snapshot.active_users = {
        "5m": int(row.w5m),
        "1h": int(row.w1h),
        "24h": int(row.w24h),
    }


def _forget() -> None:
    product_metrics.snapshot.active_users = {}


register_gauge_sampler(
    "active_users", sample_active_users, forget=_forget, every=_EVERY
)
