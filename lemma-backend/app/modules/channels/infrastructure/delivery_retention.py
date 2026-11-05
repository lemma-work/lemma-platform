"""How long settled pushes are kept.

A settled push -- ``SENT``, ``SKIPPED`` or ``DEAD``, to a member -- is deleted
``KEPT_FOR`` after it was made: it is a record of a notification, and nothing
reads it once the line is old. A send to a place is kept as long as its line:
its platform id is how a reply in a thread there finds the line it answers,
and how a late status report finds the send.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import DeliveryStatus
from app.modules.channels.infrastructure.delivery_models import (
    ChannelMessageDeliveryModel,
)

#: How long a settled push is kept.
KEPT_FOR = timedelta(days=30)


def _made_before(at: datetime) -> UUID:
    """The smallest uuid7 of ``at``: every id made earlier sorts below it."""
    millis = int(at.timestamp() * 1000)
    return UUID(int=(millis << 80) | (0x7 << 76) | (0b10 << 62))


async def prune_settled(
    session: AsyncSession, *, limit: int, now: datetime | None = None
) -> int:
    """Delete up to ``limit`` settled pushes made more than ``KEPT_FOR`` ago;
    how many. Ids are time-ordered, so it walks its index from the oldest."""
    floor = _made_before((now or datetime.now(UTC)) - KEPT_FOR)
    d = ChannelMessageDeliveryModel
    pushes = (
        select(d.id)
        .where(
            d.id < floor,
            d.recipient_user_id.is_not(None),
            # Literals: the predicate of the partial index this walks.
            d.status.in_(
                [
                    literal_column(f"'{status.value}'")
                    for status in (
                        DeliveryStatus.SENT,
                        DeliveryStatus.SKIPPED,
                        DeliveryStatus.DEAD,
                    )
                ]
            ),
        )
        .order_by(d.id)
        .limit(limit)
    )
    gone = await session.execute(delete(d).where(d.id.in_(pushes)))
    return _count(gone)


def _count(result: object) -> int:
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount) if isinstance(rowcount, int) else 0


__all__ = ["KEPT_FOR", "prune_settled"]
