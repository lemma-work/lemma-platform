"""A member's pushes of one channel, coalesced: the lines waiting to go.

One ``channel_push_pending`` row per member and channel. A line they hear is
folded into it (``fold``); when it is due the row is emptied and its lines go
as one delivery (``take``, then ``plan_pushes``). A line that names or asks
them is due at once. Any other is due at once too unless the member was
pushed from this channel within ``PUSH_WINDOW``: then it waits for the
window's end, and whatever else comes in that time goes with it -- one push
per member per channel per window, however busy the channel is.

Rows are touched in recipient order, so two writers folding lines of one
channel for many members cannot deadlock on each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, delete, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import (
    Delivery,
    DeliveryReasonCode,
    DeliveryStatus,
)
from app.modules.channels.infrastructure.delivery_models import (
    ChannelMessageDeliveryModel,
    ChannelPushPendingModel,
)
from app.modules.channels.infrastructure.models import ChannelMessageModel
from app.modules.channels.infrastructure.rows import delivery_entity

#: After a push from a channel, how long its next lines wait to go together.
PUSH_WINDOW = timedelta(minutes=5)

_P = ChannelPushPendingModel


@dataclass(frozen=True, slots=True)
class Fold:
    """Lines to fold into one member's window of a channel."""

    user_id: UUID
    #: One of them names or asks them: due now.
    urgent: bool
    from_seq: int
    #: The newest, and the one the push leads with.
    seq: int
    lines: int = 1


@dataclass(frozen=True, slots=True)
class Folded:
    user_id: UUID
    next_attempt_at: datetime
    due: bool


@dataclass(frozen=True, slots=True)
class Window:
    """The lines one push stands for."""

    user_id: UUID
    from_seq: int
    lead_seq: int
    lines: int
    urgent: bool


class PushWindows:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fold(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        folds: Sequence[Fold],
        not_before: timedelta = timedelta(0),
    ) -> list[Folded]:
        """Fold lines into each member's window; when each is now due.
        ``not_before``: none of these goes sooner (a push the hourly budget
        held, folded back), unless one is urgent."""
        if not folds:
            return []
        at = func.now() + not_before
        rows = [
            {
                "channel_id": channel_id,
                "recipient_user_id": fold.user_id,
                "pod_id": pod_id,
                "from_seq": fold.from_seq,
                "to_seq": fold.seq,
                "lead_seq": fold.seq,
                "lines": fold.lines,
                "urgent": fold.urgent,
                "next_attempt_at": func.now() if fold.urgent else at,
            }
            for fold in sorted(folds, key=lambda each: each.user_id)
        ]
        statement = insert(_P).values(rows)
        new = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[_P.channel_id, _P.recipient_user_id],
            set_={
                "from_seq": func.least(
                    func.coalesce(_P.from_seq, new.from_seq), new.from_seq
                ),
                "to_seq": func.greatest(
                    func.coalesce(_P.to_seq, new.to_seq), new.to_seq
                ),
                # The newest line that calls them, else the newest.
                "lead_seq": case(
                    (
                        new.urgent == _P.urgent,
                        func.greatest(
                            func.coalesce(_P.lead_seq, new.lead_seq), new.lead_seq
                        ),
                    ),
                    (new.urgent, new.lead_seq),
                    else_=func.coalesce(_P.lead_seq, new.lead_seq),
                ),
                "lines": _P.lines + new.lines,
                "urgent": _P.urgent | new.urgent,
                "next_attempt_at": case(
                    (new.urgent, func.now()),
                    (
                        _P.next_attempt_at.is_not(None),
                        func.greatest(_P.next_attempt_at, new.next_attempt_at),
                    ),
                    else_=func.greatest(
                        new.next_attempt_at,
                        func.coalesce(_P.pushed_at + PUSH_WINDOW, new.next_attempt_at),
                    ),
                ),
                "updated_at": func.now(),
            },
        ).returning(
            _P.recipient_user_id,
            _P.next_attempt_at,
            (_P.next_attempt_at <= func.now()).label("due"),
        )
        result = await self.session.execute(statement)
        return [
            Folded(user_id=user_id, next_attempt_at=next_at, due=bool(due))
            for user_id, next_at, due in result
        ]

    async def take(self, *, channel_id: UUID, user_ids: Sequence[UUID]) -> list[Window]:
        """Empty the windows of these members that are due now, and what they
        held. A window not due yet -- or taken already -- is left."""
        if not user_ids:
            return []
        due = await self.session.execute(
            select(_P.recipient_user_id, _P.from_seq, _P.lead_seq, _P.lines, _P.urgent)
            .where(
                _P.channel_id == channel_id,
                _P.recipient_user_id.in_(sorted(set(user_ids))),
                _P.next_attempt_at <= func.now(),
            )
            .order_by(_P.recipient_user_id)
            .with_for_update()
        )
        windows = [
            Window(
                user_id=user_id,
                from_seq=from_seq,
                lead_seq=lead_seq,
                lines=lines,
                urgent=urgent,
            )
            for user_id, from_seq, lead_seq, lines, urgent in due
        ]
        if windows:
            await self.session.execute(
                update(_P)
                .where(
                    _P.channel_id == channel_id,
                    _P.recipient_user_id.in_([each.user_id for each in windows]),
                )
                .values(
                    from_seq=None,
                    to_seq=None,
                    lead_seq=None,
                    lines=0,
                    urgent=False,
                    next_attempt_at=None,
                    pushed_at=func.now(),
                    updated_at=func.now(),
                )
            )
        return windows

    async def plan_pushes(
        self, *, channel_id: UUID, pod_id: UUID, windows: Sequence[Window]
    ) -> list[Delivery]:
        """One delivery per window, on the line it leads with. A line whose
        push the budget held (``FOLDED``) is planned again; one already
        pushed to them is not pushed twice."""
        if not windows:
            return []
        m = ChannelMessageModel
        found = await self.session.execute(
            select(m.seq, m.id).where(
                m.channel_id == channel_id,
                m.seq.in_(sorted({each.lead_seq for each in windows})),
            )
        )
        lines = dict(found.tuples().all())
        rows = [
            {
                "message_id": lines[each.lead_seq],
                "channel_id": channel_id,
                "seq": each.lead_seq,
                "pod_id": pod_id,
                "recipient_user_id": each.user_id,
                "from_seq": each.from_seq,
                "lines": each.lines,
                "urgent": each.urgent,
                "status": DeliveryStatus.PENDING.value,
                "next_attempt_at": func.now(),
            }
            for each in windows
            if each.lead_seq in lines
        ]
        if not rows:
            return []
        d = ChannelMessageDeliveryModel
        statement = insert(d).values(rows)
        new = statement.excluded
        planned = await self.session.scalars(
            statement.on_conflict_do_update(
                index_elements=[
                    d.message_id,
                    text("coalesce(source_id, recipient_user_id)"),
                ],
                set_={
                    "status": DeliveryStatus.PENDING.value,
                    "reason_code": None,
                    "reason": None,
                    "attempts": 0,
                    "claimed_at": None,
                    "next_attempt_at": func.now(),
                    "from_seq": func.least(d.from_seq, new.from_seq),
                    "lines": new.lines,
                    "urgent": new.urgent,
                    "updated_at": func.now(),
                },
                where=and_(
                    d.status == DeliveryStatus.SKIPPED.value,
                    d.reason_code == DeliveryReasonCode.FOLDED.value,
                ),
            ).returning(d)
        )
        return [delivery_entity(row) for row in planned]

    async def prune(self, *, limit: int) -> int:
        """Forget empty windows whose time has passed: the next line from
        those channels goes at once anyway."""
        stale = (
            select(_P.channel_id, _P.recipient_user_id)
            .where(
                _P.next_attempt_at.is_(None),
                _P.pushed_at < func.now() - PUSH_WINDOW,
            )
            .limit(limit)
        )
        result = await self.session.execute(
            delete(_P)
            .where(tuple_(_P.channel_id, _P.recipient_user_id).in_(stale))
            .returning(_P.channel_id)
        )
        return len(result.all())


__all__ = ["PUSH_WINDOW", "Fold", "Folded", "PushWindows", "Window"]
