"""Deliveries: each send of a line outside Lemma, and the only record of it.

A delivery row is made once per line and target (a place, or a member pushed
to) before anything is sent. A worker claims a due row, sends, and records
the outcome on the same row -- so a retried job finds ``SENT`` and stops,
and a failure is retried with backoff until it is ``DEAD``. Each claim opens
a row in ``channel_delivery_attempts`` and the outcome closes it, so the tries
that led to the outcome stay visible.

A claim is held for ``CLAIM_LEASE`` and stamps ``claimed_at``; every outcome
clears it. A claim that lapsed with ``claimed_at`` still set is a send nobody
finished -- the sender died somewhere between taking it and recording what
happened, perhaps after the platform took it. It is never sent again blind:
when the platform's id for it was noted before the call (a live stream the
answer closes) it is ``SENT``, since the message is there; where the
platform dedupes a resend by the delivery's key the sender takes it again
(``reclaim``); otherwise it is ``DEAD`` (``INTERRUPTED``), and a member can
retry it. Saying something twice is worse than saying, visibly, that it may
not have arrived.

Every time here is the database's, as a row is planned by: a sender whose
clock runs behind must not find a row it just planned not yet due. A row
waits while an earlier line's row to the same target has yet to go
(``_behind_an_older_one``), so a place reads a channel in order. Why a row is
not ``SENT`` is ``reason_code``, which code reads; ``reason`` is the sentence
a member does.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    and_,
    case,
    cast,
    exists,
    func,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from app.modules.channels.domain.entities import (
    Delivery,
    DeliveryReasonCode,
    DeliveryStatus,
)
from app.modules.channels.infrastructure.delivery_models import (
    ChannelMessageDeliveryModel,
)
from app.modules.channels.infrastructure.delivery_attempts import (
    try_ended,
    try_started,
)
from app.modules.channels.infrastructure.revisions import touch_line, touch_lines
from app.modules.channels.infrastructure.rows import MAX_LINES, delivery_entity

#: Tries before a delivery is given up on.
MAX_ATTEMPTS = 6

#: How long a sender holds a claim. Above the sender's own bound on one send
#: (``agent_surfaces`` gives up on a platform at 60 s), so a slow platform is
#: a failure it records, not a lapsed claim.
CLAIM_LEASE = timedelta(seconds=90)

#: What a member reads on a send nobody finished.
INTERRUPTED = (
    "Sending this was interrupted, so it may or may not have arrived. It was "
    "not sent again in case it did."
)

_D = ChannelMessageDeliveryModel


def is_due(status: InstrumentedAttribute[str]) -> ColumnElement[bool]:
    """``status IN ('PENDING', 'FAILED')``, spelled as literals: the predicate
    of the partial due indexes, which a bound parameter could not be proved
    to imply in a generic plan."""
    return status.in_(
        [
            literal_column(f"'{DeliveryStatus.PENDING.value}'"),
            literal_column(f"'{DeliveryStatus.FAILED.value}'"),
        ]
    )


def external_key(
    value: InstrumentedAttribute[str | None] | str,
) -> ColumnElement[UUID]:
    """A platform message id as it is indexed: its md5, as a uuid."""
    return cast(func.md5(value), PG_UUID(as_uuid=True))


def _settleable() -> ColumnElement[bool]:
    """A delivery a sender may still record an outcome on: one it is sending,
    or one settled as interrupted while it was -- the sender's own outcome
    is better than that guess. A platform's report (``undelivered``) and
    every other outcome stand."""
    return or_(
        is_due(_D.status),
        and_(
            _D.status == DeliveryStatus.DEAD.value,
            _D.reason_code == DeliveryReasonCode.INTERRUPTED.value,
        ),
    )


def backoff(attempts: int, *, retry_after: float | None = None) -> timedelta:
    """How long after the ``attempts``-th failure to try again: 30 s, 2 min,
    8 min, 32 min, ~2 h -- or longer, when the platform asked for longer."""
    wait = timedelta(seconds=30 * 4 ** max(0, attempts - 1))
    if retry_after is not None and retry_after > 0:
        return max(wait, timedelta(seconds=retry_after))
    return wait


def _behind_an_older_one() -> ColumnElement[bool]:
    """A delivery with an older one to the same target still to go.

    A channel's lines reach one place -- and one member's pushes -- in the
    order they were written: a row waits while a row for an earlier line of
    the same channel, to the same target, is pending, being sent, or failed
    and waiting out its backoff (a ``Retry-After`` holds back everything
    behind it). Lines of other channels are not held: a member's pushes from
    one conversation do not wait on another's. One probe of the target's due
    index (``ix_cmd_due_source`` / ``ix_cmd_due_recipient``), never a walk of
    the channel's history.
    """
    older = aliased(ChannelMessageDeliveryModel)
    earlier = (
        is_due(older.status),
        older.channel_id == _D.channel_id,
        older.seq < _D.seq,
    )
    return or_(
        and_(
            _D.source_id.is_not(None),
            exists().where(older.source_id == _D.source_id, *earlier),
        ),
        and_(
            _D.recipient_user_id.is_not(None),
            exists().where(older.recipient_user_id == _D.recipient_user_id, *earlier),
        ),
    )


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def plan(
        self,
        *,
        message_id: UUID,
        channel_id: UUID,
        seq: int,
        pod_id: UUID,
        source_id: UUID | None = None,
        recipient_user_id: UUID | None = None,
    ) -> Delivery:
        """The line's delivery to this target, made once; a second plan finds it."""
        await self.session.execute(
            insert(_D)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                seq=seq,
                pod_id=pod_id,
                source_id=source_id,
                recipient_user_id=recipient_user_id,
                status=DeliveryStatus.PENDING.value,
                next_attempt_at=func.now(),
            )
            .on_conflict_do_nothing()
        )
        target = _D.source_id if source_id is not None else _D.recipient_user_id
        row = await self.session.scalar(
            select(_D).where(
                _D.message_id == message_id, target == (source_id or recipient_user_id)
            )
        )
        assert row is not None
        return delivery_entity(row)

    async def get(self, delivery_id: UUID) -> Delivery | None:
        row = await self.session.get(ChannelMessageDeliveryModel, delivery_id)
        return delivery_entity(row) if row else None

    async def claim(self, delivery_id: UUID) -> Delivery | None:
        """Take a due delivery to send it, for ``CLAIM_LEASE``. ``None`` if it
        is not due, waits behind an older one to its target, or is a claim
        that lapsed (``interrupted``). By the database's clock, as it was
        planned by, so a sender's clock running behind cannot leave a row
        just planned not yet due."""
        row = await self.session.scalar(
            update(_D)
            .where(
                _D.id == delivery_id,
                is_due(_D.status),
                _D.next_attempt_at <= func.now(),
                _D.claimed_at.is_(None),
                ~_behind_an_older_one(),
            )
            .values(
                attempts=_D.attempts + 1,
                claimed_at=func.now(),
                next_attempt_at=func.now() + CLAIM_LEASE,
                updated_at=func.now(),
            )
            .returning(_D)
        )
        if row is None:
            return None
        await try_started(self.session, row.id)
        return delivery_entity(row)

    async def lapsed(self, delivery_id: UUID) -> Delivery | None:
        """The delivery, when it is a claim whose lease ran out unfinished."""
        row = await self.session.scalar(
            select(_D).where(
                _D.id == delivery_id,
                is_due(_D.status),
                _D.claimed_at.is_not(None),
                _D.next_attempt_at <= func.now(),
            )
        )
        return delivery_entity(row) if row is not None else None

    async def reclaim(self, delivery_id: UUID) -> Delivery | None:
        """Take a lapsed claim to send it again: only for a send whose
        platform dedupes a resend by the delivery's key, so a send that did
        arrive is not repeated (``agent_surfaces.delivery_outcome``)."""
        row = await self.session.scalar(
            update(_D)
            .where(
                _D.id == delivery_id,
                is_due(_D.status),
                _D.claimed_at.is_not(None),
                _D.next_attempt_at <= func.now(),
                _D.external_message_id.is_(None),
                _D.attempts < MAX_ATTEMPTS,
            )
            .values(
                attempts=_D.attempts + 1,
                claimed_at=func.now(),
                next_attempt_at=func.now() + CLAIM_LEASE,
                updated_at=func.now(),
            )
            .returning(_D)
        )
        if row is None:
            return None
        await try_ended(
            self.session, row.id, DeliveryStatus.FAILED, DeliveryReasonCode.INTERRUPTED
        )
        await try_started(self.session, row.id)
        return delivery_entity(row)

    async def interrupted(self, delivery_id: UUID) -> Delivery | None:
        """Settle a claim that lapsed unfinished, without sending it again:
        ``SENT`` when its platform id was noted before the call, else ``DEAD``."""
        known = _D.external_message_id.is_not(None)
        row = await self.session.scalar(
            update(_D)
            .where(
                _D.id == delivery_id,
                is_due(_D.status),
                _D.claimed_at.is_not(None),
                _D.next_attempt_at <= func.now(),
            )
            .values(
                status=case(
                    (known, DeliveryStatus.SENT.value),
                    else_=DeliveryStatus.DEAD.value,
                ),
                reason_code=case(
                    (known, None), else_=DeliveryReasonCode.INTERRUPTED.value
                ),
                reason=case((known, None), else_=INTERRUPTED),
                sent_at=case((known, func.now()), else_=None),
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(_D)
        )
        if row is None:
            return None
        entity = delivery_entity(row)
        await try_ended(self.session, row.id, entity.status, entity.reason_code)
        await self._shown_again([(row.message_id, row.source_id)])
        return entity

    async def noted(self, delivery_id: UUID, *, external_message_id: str) -> None:
        """The platform's id for a send, as soon as it is known: before the
        call that finishes a message already there (a claim lapsing after
        this is known to have arrived), or the moment the platform answers (a
        status report about it finds the row even before it is ``SENT``)."""
        await self.session.execute(
            update(_D)
            .where(_D.id == delivery_id, _D.claimed_at.is_not(None))
            .values(external_message_id=external_message_id, updated_at=func.now())
        )

    async def unnoted(self, delivery_id: UUID) -> None:
        """A noted id that no longer stands for anything there (a live
        stream taken down before the answer replaced it)."""
        await self.session.execute(
            update(_D)
            .where(_D.id == delivery_id, _D.claimed_at.is_not(None))
            .values(external_message_id=None, updated_at=func.now())
        )

    async def _settle(
        self,
        delivery_id: UUID,
        status: DeliveryStatus,
        *,
        code: DeliveryReasonCode | None,
        reason: str | None,
        platform: str | None,
        provider_status: str | None,
        **values: object,
    ) -> Delivery | None:
        """Record an outcome, and end the try that led to it; the delivery as
        it now stands, or ``None`` when something else settled it first."""
        row = await self.session.scalar(
            update(_D)
            .where(_D.id == delivery_id, _settleable())
            .values(
                status=status.value,
                platform=platform,
                reason_code=code.value if code is not None else None,
                reason=reason,
                claimed_at=None,
                updated_at=func.now(),
                **values,
            )
            .returning(_D)
        )
        if row is None:
            return None
        await try_ended(
            self.session, delivery_id, status, code, provider_status=provider_status
        )
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def sent(
        self,
        delivery_id: UUID,
        *,
        platform: str,
        external_message_id: str | None,
        provider_status: str | None = None,
        reason: str | None = None,
    ) -> None:
        await self._settle(
            delivery_id,
            DeliveryStatus.SENT,
            code=None,
            reason=reason,
            platform=platform,
            provider_status=provider_status,
            external_message_id=func.coalesce(
                external_message_id, _D.external_message_id
            ),
            sent_at=func.now(),
            next_attempt_at=None,
        )

    async def skipped(
        self,
        delivery_id: UUID,
        *,
        code: DeliveryReasonCode,
        reason: str,
        platform: str | None,
        provider_status: str | None = None,
    ) -> Delivery | None:
        """Not sent, on purpose."""
        return await self._settle(
            delivery_id,
            DeliveryStatus.SKIPPED,
            code=code,
            reason=reason,
            platform=platform,
            provider_status=provider_status,
            next_attempt_at=None,
        )

    async def failed(
        self,
        delivery: Delivery,
        *,
        code: DeliveryReasonCode,
        reason: str,
        platform: str | None,
        retry_after: float | None = None,
        provider_status: str | None = None,
    ) -> Delivery:
        """It did not go: due again after backoff -- at least as long as the
        platform asked (``retry_after``, seconds) -- or DEAD after the last try."""
        dead = delivery.attempts >= MAX_ATTEMPTS
        wait = backoff(delivery.attempts, retry_after=retry_after)
        settled = await self._settle(
            delivery.id,
            DeliveryStatus.DEAD if dead else DeliveryStatus.FAILED,
            code=code,
            reason=reason,
            platform=platform,
            provider_status=provider_status,
            next_attempt_at=None if dead else func.now() + wait,
        )
        # ``None``: a platform's report settled it meanwhile; that stands.
        return settled or await self.get(delivery.id) or delivery

    async def dead(
        self,
        delivery_id: UUID,
        *,
        code: DeliveryReasonCode,
        reason: str,
        platform: str | None,
        provider_status: str | None = None,
    ) -> None:
        """Given up now, not after backoff: trying again repeats what the
        person already has (half a long message), or meets the same refusal."""
        await self._settle(
            delivery_id,
            DeliveryStatus.DEAD,
            code=code,
            reason=reason,
            platform=platform,
            provider_status=provider_status,
            next_attempt_at=None,
        )

    async def retry(self, delivery_id: UUID) -> Delivery | None:
        """A member asked to try a failed or given-up delivery again, now: one
        more try, counted with the others (its tries are its attempt rows), so
        failing again on the last gives it up. Why it failed is cleared until
        this try says."""
        row = await self.session.scalar(
            update(_D)
            .where(
                _D.id == delivery_id,
                _D.status.in_([DeliveryStatus.FAILED.value, DeliveryStatus.DEAD.value]),
            )
            .values(
                status=DeliveryStatus.PENDING.value,
                reason_code=None,
                reason=None,
                claimed_at=None,
                next_attempt_at=func.now(),
                updated_at=func.now(),
            )
            .returning(_D)
        )
        if row is None:
            return None
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def undelivered(self, delivery_id: UUID, *, reason: str) -> bool:
        """The platform took a send and reported later that it never arrived:
        given up, since sending it again would meet the same refusal. Only
        the delivery the report names (``by_external``), whether it is
        ``SENT`` yet or still being recorded -- a report can land before the
        sender's own outcome does, and the sender's ``sent`` then leaves it.
        Whether it changed anything."""
        row = await self.session.execute(
            update(_D)
            .where(
                _D.id == delivery_id,
                or_(
                    _D.status == DeliveryStatus.SENT.value,
                    and_(is_due(_D.status), _D.claimed_at.is_not(None)),
                ),
            )
            .values(
                status=DeliveryStatus.DEAD.value,
                reason_code=DeliveryReasonCode.UNDELIVERED.value,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(_D.message_id, _D.source_id)
        )
        changed = [(message_id, source_id) for message_id, source_id in row]
        await self._shown_again(changed)
        return bool(changed)

    async def by_external(
        self, *, platform: str, external_message_id: str, pod_id: UUID | None = None
    ) -> Delivery | None:
        """The delivery a platform's status report names: the newest with that
        id, in ``pod_id`` when the report says whose it is."""
        statement = select(_D).where(
            external_key(_D.external_message_id) == external_key(external_message_id),
            _D.external_message_id == external_message_id,
            # A send still being recorded has its id noted and no platform yet.
            or_(_D.platform == platform, _D.platform.is_(None)),
        )
        if pod_id is not None:
            statement = statement.where(_D.pod_id == pod_id)
        row = await self.session.scalar(
            statement.order_by(_D.created_at.desc()).limit(1)
        )
        return delivery_entity(row) if row is not None else None

    async def sent_as(self, source_id: UUID, external_message_id: str) -> UUID | None:
        """The line written in Lemma that went to this place as that platform
        message, if one did: what a reply there in its thread answers."""
        return await self.session.scalar(
            select(_D.message_id)
            .where(
                _D.source_id == source_id,
                external_key(_D.external_message_id)
                == external_key(external_message_id),
                _D.external_message_id == external_message_id,
            )
            .limit(1)
        )

    async def next_for_target(self, delivery: Delivery) -> UUID | None:
        """The oldest delivery to ``delivery``'s target that is due now and no
        longer waits behind another: what to announce once ``delivery`` is
        settled, so the place's next line goes without waiting for a sweep."""
        if delivery.source_id is not None:
            target, column = delivery.source_id, _D.source_id
        else:
            target, column = delivery.recipient_user_id, _D.recipient_user_id
        return await self.session.scalar(
            select(_D.id)
            .where(
                column == target,
                is_due(_D.status),
                _D.id != delivery.id,
                _D.claimed_at.is_(None),
                _D.next_attempt_at <= func.now(),
                ~_behind_an_older_one(),
            )
            .order_by(_D.channel_id, _D.seq)
            .limit(1)
        )

    async def _shown_again(self, changed: Iterable[tuple[UUID, UUID | None]]) -> None:
        """A send to a place moved: the line's writer sees where it went on
        the line (``ChannelMessageResponse.delivered``), so it is sent to its
        watchers again. A push to a member is not shown on the line."""
        lines = {
            message_id for message_id, source_id in changed if source_id is not None
        }
        if len(lines) == 1:
            await touch_line(self.session, next(iter(lines)))
        else:
            await touch_lines(self.session, lines)

    async def of_messages(
        self, message_ids: Sequence[UUID], *, to_places: bool = False
    ) -> list[Delivery]:
        """These lines' sends -- ``to_places``: not pushes to members."""
        if not message_ids:
            return []
        statement = select(_D).where(_D.message_id.in_(list(message_ids)[:MAX_LINES]))
        if to_places:
            statement = statement.where(_D.source_id.is_not(None))
        rows = await self.session.scalars(statement.order_by(_D.created_at))
        return [delivery_entity(row) for row in rows]

    async def due(self, *, limit: int) -> list[UUID]:
        """Deliveries due now, oldest first: what the sweeper hands out. Not
        one waiting behind an older one to its target: that one goes first,
        and announces it (``next_for_target``)."""
        rows = await self.session.scalars(
            select(_D.id)
            .where(
                is_due(_D.status),
                _D.next_attempt_at <= func.now(),
                ~_behind_an_older_one(),
            )
            .order_by(_D.next_attempt_at)
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return list(rows)
