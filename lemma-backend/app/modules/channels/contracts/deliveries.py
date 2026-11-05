"""Deliveries: what whoever owns the platforms reads and records as it sends.

Channels plans a delivery for every send outside Lemma and announces it
(``ChannelDeliveryDue``). The sender claims it, sends, and records the outcome
here -- with the platform's id for what it sent, which is how a platform's
later status report finds it, and a ``DeliveryReasonCode`` beside the
sentence a member reads for anything not sent. A claimed delivery is never
sent twice: one whose sender died mid-send is settled by
``delivery_interrupted``, not resent.

A push to a member stands for every line of one channel they heard since
their last push from it (``Delivery.from_seq`` .. ``seq``, ``lines`` of
them); ``Delivery.urgent`` when one names or asks them. One the hourly budget
holds is ``delivery_folded``: its lines go with the member's next push from
that channel.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import ChannelDeliveryDue
from app.modules.channels.domain.entities import Delivery, DeliveryReasonCode
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.services.pushes import fold_back

__all__ = [
    "announce_next_for_target",
    "claim_delivery",
    "deliveries_of",
    "delivery_by_external",
    "delivery_by_id",
    "delivery_dead",
    "delivery_failed",
    "delivery_folded",
    "delivery_interrupted",
    "delivery_noted",
    "delivery_sent",
    "delivery_skipped",
    "delivery_undelivered",
    "delivery_unnoted",
    "due_deliveries",
    "due_fact",
    "lapsed_delivery",
    "reclaim_delivery",
    "retry_delivery",
]


async def delivery_by_id(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    return await DeliveryRepository(uow.session).get(delivery_id)


async def claim_delivery(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    """Take a due delivery to send it; ``None`` when it is not due -- sent,
    skipped, given up, being sent by somebody else, waiting behind an earlier
    line to the same target, or a claim that lapsed unfinished
    (``lapsed_delivery``)."""
    return await DeliveryRepository(uow.session).claim(delivery_id)


async def lapsed_delivery(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    """The delivery, when it is a claim whose sender died before recording
    what happened."""
    return await DeliveryRepository(uow.session).lapsed(delivery_id)


async def reclaim_delivery(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    """Take a lapsed claim to send it again -- only where the platform dedupes
    a resend by the delivery's key, so what did arrive is not repeated."""
    return await DeliveryRepository(uow.session).reclaim(delivery_id)


async def delivery_interrupted(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    """Settle a claim whose sender died before recording what happened, without
    sending it again: ``SENT`` when its platform id was noted before the call
    (``delivery_noted``), else ``DEAD`` (``INTERRUPTED``). ``None`` when it is
    no such claim."""
    return await DeliveryRepository(uow.session).interrupted(delivery_id)


async def delivery_noted(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID, *, external_message_id: str
) -> None:
    """The platform's id for a claimed send, as soon as it is known: before
    the call that finishes a live stream in place, or the moment the platform
    answers, so its status report finds the row before ``SENT`` is recorded."""
    await DeliveryRepository(uow.session).noted(
        delivery_id, external_message_id=external_message_id
    )


async def delivery_unnoted(uow: SqlAlchemyUnitOfWork, delivery_id: UUID) -> None:
    """Forget a noted id that no longer stands for a message there: a live
    stream taken down, so a lapse is not read as the answer having arrived."""
    await DeliveryRepository(uow.session).unnoted(delivery_id)


async def delivery_sent(
    uow: SqlAlchemyUnitOfWork,
    delivery_id: UUID,
    *,
    platform: str,
    external_message_id: str | None,
    provider_status: str | None = None,
    reason: str | None = None,
) -> None:
    """It went. ``reason``: what a member should know of where it waits --
    a web chat holds it until the visitor is back."""
    await DeliveryRepository(uow.session).sent(
        delivery_id,
        platform=platform,
        external_message_id=external_message_id,
        provider_status=provider_status,
        reason=reason,
    )


async def delivery_skipped(
    uow: SqlAlchemyUnitOfWork,
    delivery_id: UUID,
    *,
    code: DeliveryReasonCode,
    reason: str,
    platform: str | None = None,
    provider_status: str | None = None,
) -> None:
    """Not sent, on purpose: why, and the reason a member reads."""
    await DeliveryRepository(uow.session).skipped(
        delivery_id,
        code=code,
        reason=reason,
        platform=platform,
        provider_status=provider_status,
    )


async def delivery_folded(
    uow: SqlAlchemyUnitOfWork,
    delivery: Delivery,
    *,
    reason: str,
    hold_for: timedelta,
) -> None:
    """A push the member's hourly budget held: ``SKIPPED`` (``FOLDED``), and
    its lines go with their next push from the channel -- no sooner than
    ``hold_for``, unless a line naming or asking them comes first."""
    settled = await DeliveryRepository(uow.session).skipped(
        delivery.id, code=DeliveryReasonCode.FOLDED, reason=reason, platform=None
    )
    if settled is not None:
        await fold_back(uow, settled, hold_for=hold_for)


async def delivery_failed(
    uow: SqlAlchemyUnitOfWork,
    delivery: Delivery,
    *,
    code: DeliveryReasonCode,
    reason: str,
    platform: str | None = None,
    retry_after: float | None = None,
    provider_status: str | None = None,
) -> Delivery:
    """It did not go: due again after backoff -- no sooner than the platform's
    ``retry_after`` (seconds) -- or given up after the last try."""
    return await DeliveryRepository(uow.session).failed(
        delivery,
        code=code,
        reason=reason,
        platform=platform,
        retry_after=retry_after,
        provider_status=provider_status,
    )


async def delivery_dead(
    uow: SqlAlchemyUnitOfWork,
    delivery_id: UUID,
    *,
    code: DeliveryReasonCode,
    reason: str,
    platform: str | None = None,
    provider_status: str | None = None,
) -> None:
    """Given up at once: a retry would repeat what already arrived (part of a
    long message) or meet the same refusal."""
    await DeliveryRepository(uow.session).dead(
        delivery_id,
        code=code,
        reason=reason,
        platform=platform,
        provider_status=provider_status,
    )


async def retry_delivery(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    return await DeliveryRepository(uow.session).retry(delivery_id)


async def delivery_undelivered(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID, *, reason: str
) -> bool:
    """A send the platform took and later reported never arrived: ``DEAD``
    (``UNDELIVERED``) with ``reason`` -- that delivery only, found by
    ``delivery_by_external``, and even while its sender is still recording
    it. Whether it changed."""
    return await DeliveryRepository(uow.session).undelivered(delivery_id, reason=reason)


async def delivery_by_external(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    external_message_id: str,
    pod_id: UUID | None = None,
) -> Delivery | None:
    """The delivery a platform's status report names, in ``pod_id`` when the
    report says whose it is."""
    return await DeliveryRepository(uow.session).by_external(
        platform=platform, external_message_id=external_message_id, pod_id=pod_id
    )


async def announce_next_for_target(
    uow: SqlAlchemyUnitOfWork, delivery: Delivery
) -> None:
    """Once ``delivery`` is settled, announce the next delivery to its target
    that was waiting behind it, so a place's lines keep going in order
    without waiting for the sweeper."""
    following = await DeliveryRepository(uow.session).next_for_target(delivery)
    if following is None:
        return
    queued = await DeliveryRepository(uow.session).get(following)
    if queued is not None:
        await facts.emit(uow, due_fact(queued))


async def deliveries_of(
    uow: SqlAlchemyUnitOfWork, message_ids: Sequence[UUID]
) -> list[Delivery]:
    return await DeliveryRepository(uow.session).of_messages(message_ids)


async def due_deliveries(uow: SqlAlchemyUnitOfWork, *, limit: int = 100) -> list[UUID]:
    return await DeliveryRepository(uow.session).due(limit=limit)


def due_fact(delivery: Delivery) -> ChannelDeliveryDue:
    """The announcement that ``delivery`` is due, keyed by its target so one
    place's sends run one at a time."""
    return ChannelDeliveryDue(
        pod_id=delivery.pod_id, delivery_id=delivery.id, target=delivery.target_key
    )
