"""Each try of a delivery, and how long the settled ones are kept.

A try begins when a sender claims a delivery and ends with what it recorded,
so ``channel_delivery_attempts`` says what each try ran into -- a 429 and
then a send is two rows -- where the delivery keeps only how it ended. A try
with no end is a claim that lapsed.

**Retention.** A settled push -- ``SENT``, ``SKIPPED`` or ``DEAD``, to a
member -- is deleted ``KEPT_FOR`` after it was made, with its tries: it is a
record of a notification, and nothing reads it once the line is old. A send
to a place is kept as long as its line: its platform id is how a reply in a
thread there finds the line it answers, and how a late status report finds
the send. Its tries go after ``KEPT_FOR`` like any other.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import DeliveryReasonCode, DeliveryStatus
from app.modules.channels.infrastructure.delivery_models import (
    ChannelDeliveryAttemptModel,
    ChannelMessageDeliveryModel,
)

#: How long a settled push, and any delivery's tries, are kept.
KEPT_FOR = timedelta(days=30)

_A = ChannelDeliveryAttemptModel


async def try_started(session: AsyncSession, delivery_id: UUID) -> None:
    """A try begins: the next number after the delivery's last try (a
    member's retry starts ``attempts`` again; the tries go on counting)."""
    number = (
        select(func.coalesce(func.max(_A.n), 0) + 1)
        .where(_A.delivery_id == delivery_id)
        .scalar_subquery()
    )
    await session.execute(insert(_A).values(delivery_id=delivery_id, n=number))


async def try_ended(
    session: AsyncSession,
    delivery_id: UUID,
    outcome: DeliveryStatus,
    code: DeliveryReasonCode | None,
    *,
    provider_status: str | None = None,
) -> None:
    """What the try in flight ended as."""
    await session.execute(
        update(_A)
        .where(_A.delivery_id == delivery_id, _A.finished_at.is_(None))
        .values(
            finished_at=func.now(),
            outcome=outcome.value,
            error_code=code.value if code is not None else None,
            provider_status=provider_status,
        )
    )


def _made_before(at: datetime) -> UUID:
    """The smallest uuid7 of ``at``: every id made earlier sorts below it."""
    millis = int(at.timestamp() * 1000)
    return UUID(int=(millis << 80) | (0x7 << 76) | (0b10 << 62))


async def prune_settled(
    session: AsyncSession, *, limit: int, now: datetime | None = None
) -> tuple[int, int]:
    """Delete up to ``limit`` settled pushes made more than ``KEPT_FOR`` ago,
    and up to ``limit`` older tries; how many of each. Ids are time-ordered,
    so both walk their index from the oldest."""
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
    tries = (
        select(_A.delivery_id)
        .where(_A.delivery_id < floor)
        .order_by(_A.delivery_id)
        .limit(limit)
        .distinct()
    )
    old = await session.execute(delete(_A).where(_A.delivery_id.in_(tries)))
    return _count(gone), _count(old)


def _count(result: object) -> int:
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount) if isinstance(rowcount, int) else 0


__all__ = ["KEPT_FOR", "prune_settled", "try_ended", "try_started"]
