"""What a contact's merge or forgetting does to the channels.

**A merge** folds the merged contact's channel into the survivor's: both
channels' lines are numbered again together, in the order they were said
(``fold_lines``), and what hangs from a line follows it by its foreign keys;
each member's read position is carried, so what they had read stays read. Both channel rows are locked first, in id order
-- the order every writer that takes two channel locks takes them in -- so a
line written to either while the fold runs either lands before it and is moved,
or waits and finds its channel gone. The merged channel is dropped only after
the conversations working in it have moved (``drop``), since dropping it
unplaces them.

**Forgetting** removes the contact's own channel -- the pod's conversation with
them, every line in it about them -- and, everywhere else, empties their lines
in place: the line keeps its number and its thread, said by nobody, so the
members' replies and the agents' answers around it stay. The answers an agent
gave them keep their words but no longer say whom they were for.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from app.core.infrastructure.db.base import Base
from app.modules.channels.domain.entities import (
    ChannelKind,
    DeliveryReasonCode,
    DeliveryStatus,
)
from app.modules.channels.infrastructure.cases import CaseRepository
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.fold_lines import (
    carry_read_state,
    recount_read_replies,
    defer_place_checks,
    merge_lines,
    renumber_deliveries,
)
from app.modules.channels.infrastructure.mentions import drop_mentions
from app.modules.channels.infrastructure.revisions import log_lines, next_rev
from app.modules.channels.infrastructure.rows import now

#: Every column of the channels' tables that names a contact, and what a merge
#: does to it: the merged contact's own channel folds into the survivor's
#: (``channels.contact_id``); these are renamed in place; and these are
#: renamed unless the survivor is named on the same row's key already (one
#: mention per line and person, one seat per channel and person), when the
#: merged contact's row goes. A new foreign key to ``contacts.id`` belongs in
#: one of them (``contacts/tests/unit/test_merge_covers_every_contact_key``).
RENAMED_ON_MERGE: tuple[InstrumentedAttribute[UUID | None], ...] = (
    ChannelMessageModel.sender_contact_id,
    ChannelMessageModel.on_behalf_contact_id,
    ChannelQuestionModel.awaits_contact_id,
    ChannelQuestionModel.answered_by_contact_id,
)
DEDUPED_ON_MERGE: tuple[
    tuple[InstrumentedAttribute[UUID | None], InstrumentedAttribute[UUID]], ...
] = (
    (ChannelMentionModel.contact_id, ChannelMentionModel.message_id),
    (ChannelMemberModel.contact_id, ChannelMemberModel.channel_id),
)

#: What a member reads on a send of a line whose writer was forgotten.
FORGOTTEN = "The person who wrote this was forgotten, so it was not sent."


@dataclass(frozen=True, slots=True)
class Fold:
    """A merged contact's channel, emptied into the survivor's."""

    merged_channel_id: UUID
    survivor_channel_id: UUID


class ContactChannels:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- a contact who turned out to be another ----------------------------------

    async def fold(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> Fold | None:
        """Move the merged contact's channel into the survivor's; ``None`` when
        the merged contact had none. ``survivor_id`` must be the current
        survivor, not one since merged away."""
        merged_channel = await self._channel_of(pod_id, merged_id)
        if merged_channel is None:
            await self._rename_contact(merged_id=merged_id, survivor_id=survivor_id)
            return None
        survivor_channel = await ChannelRepository(self.session).for_contact(
            pod_id=pod_id, contact_id=survivor_id
        )
        locked = await self._lock(merged_channel, survivor_channel.id)
        await self._rename_contact(merged_id=merged_id, survivor_id=survivor_id)
        merged, head = locked.get(merged_channel), locked.get(survivor_channel.id)
        if merged is None or head is None:
            return None
        base = await self._last_seq(head)
        await self._free_client_ids(merged.id, head.id)
        # The survivor's subscribers are sent the lines and fields it gained;
        # the merged channel's find it gone when it is dropped.
        await next_rev(self.session, head.id, channel_changed=True)
        moved = await self._move_lines(pod_id, merged.id, head.id)
        await self._move_places(merged.id, head.id)
        await self._move_seats(merged.id, head.id)
        await CaseRepository(self.session).fold(merged, head)
        _settle(head, merged, survivor_had_lines=base > 0)
        head.last_seq = base + moved
        head.replies = head.replies + merged.replies
        await self.session.flush()
        await recount_read_replies(self.session, channel_id=head.id)
        return Fold(merged_channel_id=merged.id, survivor_channel_id=head.id)

    async def drop(self, fold: Fold) -> None:
        """The folded channel, empty now, goes."""
        await ChannelRepository(self.session).delete(fold.merged_channel_id)

    async def _channel_of(self, pod_id: UUID, contact_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(ChannelModel.id).where(
                ChannelModel.pod_id == pod_id,
                ChannelModel.kind == ChannelKind.CONTACT.value,
                ChannelModel.contact_id == contact_id,
            )
        )

    async def _lock(self, *channel_ids: UUID) -> dict[UUID, ChannelModel]:
        rows = await self.session.scalars(
            select(ChannelModel)
            .where(ChannelModel.id.in_(channel_ids))
            .order_by(ChannelModel.id)
            .with_for_update()
            # Read again under the lock: a line committed since the row was
            # first loaded moved its head.
            .execution_options(populate_existing=True)
        )
        return {row.id: row for row in rows}

    async def _last_seq(self, head: ChannelModel) -> int:
        """The survivor's newest number, from its lines as well as its head:
        the moved lines go after whichever is higher."""
        highest = await self.session.scalar(
            select(func.max(ChannelMessageModel.seq)).where(
                ChannelMessageModel.channel_id == head.id
            )
        )
        return max(head.last_seq, int(highest or 0))

    async def _free_client_ids(self, merged_id: UUID, survivor_id: UUID) -> None:
        """A client id both channels used is one line per channel: the moved
        one is renamed, the same way every time the fold runs."""
        line, theirs = ChannelMessageModel, aliased(ChannelMessageModel)
        await self.session.execute(
            update(line)
            .where(
                line.channel_id == merged_id,
                line.client_message_id.in_(
                    select(theirs.client_message_id).where(
                        theirs.channel_id == survivor_id,
                        theirs.client_message_id.is_not(None),
                    )
                ),
            )
            .values(
                client_message_id=func.concat(
                    "fold:",
                    func.md5(func.concat(str(merged_id), ":", line.client_message_id)),
                )
            )
            .execution_options(synchronize_session=False)
        )

    async def _move_lines(
        self, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> int:
        """Both channels' lines, numbered together in the survivor; how many
        came from the merged one. Read positions and deliveries' numbers are
        carried first, from the numbers as they were."""
        line = ChannelMessageModel
        incoming = int(
            await self.session.scalar(
                select(func.count()).where(line.channel_id == merged_id)
            )
            or 0
        )
        await defer_place_checks(self.session)
        await carry_read_state(
            self.session, pod_id=pod_id, merged_id=merged_id, survivor_id=survivor_id
        )
        await renumber_deliveries(
            self.session, merged_id=merged_id, survivor_id=survivor_id
        )
        changed = await merge_lines(
            self.session, merged_id=merged_id, survivor_id=survivor_id
        )
        await _log_changed(self.session, survivor_id, changed)
        return incoming

    async def _move_places(self, merged_id: UUID, survivor_id: UUID) -> None:
        """The merged channel's places feed the survivor's. Its questions,
        mentions and the rest follow their lines by their foreign keys."""
        source = ChannelSourceModel
        await self.session.execute(
            update(source)
            .where(source.channel_id == merged_id)
            .values(channel_id=survivor_id)
            .execution_options(synchronize_session=False)
        )

    async def _move_seats(self, merged_id: UUID, survivor_id: UUID) -> None:
        """Members and agents seated only in the merged channel keep a seat;
        where both seat them, the survivor's seat stands."""
        seat, there = ChannelMemberModel, aliased(ChannelMemberModel)
        for key in ("user_id", "agent_id"):
            column, other = getattr(seat, key), getattr(there, key)
            await self.session.execute(
                update(seat)
                .where(
                    seat.channel_id == merged_id,
                    column.is_not(None),
                    ~select(there.id)
                    .where(there.channel_id == survivor_id, other == column)
                    .exists(),
                )
                .values(channel_id=survivor_id)
                .execution_options(synchronize_session=False)
            )

    async def _rename_contact(self, *, merged_id: UUID, survivor_id: UUID) -> None:
        """Lines, questions, mentions and seats that name the merged contact
        name the survivor; where both are named, the survivor's stays."""
        for column in RENAMED_ON_MERGE:
            await self.session.execute(
                update(_table_of(column))
                .where(column == merged_id)
                .values({column.key: survivor_id})
                .execution_options(synchronize_session=False)
            )
        for column, key in DEDUPED_ON_MERGE:
            table = _table_of(column)
            both = select(key).where(column == survivor_id)
            await self.session.execute(
                delete(table)
                .where(column == merged_id, key.in_(both))
                .execution_options(synchronize_session=False)
            )
            await self.session.execute(
                update(table)
                .where(column == merged_id)
                .values({column.key: survivor_id})
                .execution_options(synchronize_session=False)
            )

    # -- a contact who proved to be a member ------------------------------------

    async def became_member(
        self, *, pod_id: UUID, contact_id: UUID, user_id: UUID
    ) -> int:
        """What the contact said in the pod's groups, the member said: their
        lines and seats there are the member's. Not the contact's own channel,
        a conversation the pod had with somebody it took for a stranger; nor
        an answer an agent made for them, which was made from what is Public
        and says so. How many lines changed hands. Channels in id order, each
        locked first, the order a new line takes it in."""
        own = await self._own_channels(pod_id, [contact_id])
        spoken = await self._spoken_in(pod_id, [contact_id])
        seated = await self._seated_in(pod_id, [contact_id])
        line, seat = ChannelMessageModel, ChannelMemberModel
        moved = 0
        for channel_id in sorted((spoken | seated) - own):
            await self._lock_one(channel_id)
            said = list(
                await self.session.scalars(
                    update(line)
                    .where(
                        line.channel_id == channel_id,
                        line.sender_contact_id == contact_id,
                    )
                    .values(
                        sender_kind="MEMBER",
                        sender_user_id=user_id,
                        sender_contact_id=None,
                    )
                    .returning(line.id)
                    .execution_options(synchronize_session=False)
                )
            )
            await _log_changed(self.session, channel_id, said)
            moved += len(said)
            already = select(seat.channel_id).where(
                seat.channel_id == channel_id, seat.user_id == user_id
            )
            await self.session.execute(
                delete(seat)
                .where(
                    seat.channel_id == channel_id,
                    seat.contact_id == contact_id,
                    seat.channel_id.in_(already),
                )
                .execution_options(synchronize_session=False)
            )
            await self.session.execute(
                update(seat)
                .where(seat.channel_id == channel_id, seat.contact_id == contact_id)
                .values(contact_id=None, user_id=user_id, role="member")
                .execution_options(synchronize_session=False)
            )
            await next_rev(self.session, channel_id, channel_changed=True)
        return moved

    # -- a contact forgotten -----------------------------------------------------

    async def forget(self, *, pod_id: UUID, contact_ids: Collection[UUID]) -> int:
        """Take these contacts out of the pod's channels, before their rows go.

        Returns how many of their lines elsewhere were emptied. Channels are
        changed in id order, as every writer holding several takes them.
        """
        ids = sorted(set(contact_ids))
        if not ids:
            return 0
        own = await self._own_channels(pod_id, ids)
        spoken = await self._spoken_in(pod_id, ids)
        seated = await self._seated_in(pod_id, ids)
        seat = ChannelMemberModel
        emptied = 0
        channels = ChannelRepository(self.session)
        for channel_id in sorted(own | spoken | seated):
            if channel_id in own:
                await channels.delete(channel_id)
                continue
            if channel_id in seated:
                await next_rev(self.session, channel_id, channel_changed=True)
            if channel_id in spoken:
                emptied += await self._empty_lines(channel_id, ids)
        await self.session.execute(
            delete(seat)
            .where(seat.pod_id == pod_id, seat.contact_id.in_(ids))
            .execution_options(synchronize_session=False)
        )
        return emptied

    async def _own_channels(self, pod_id: UUID, ids: list[UUID]) -> set[UUID]:
        return set(
            await self.session.scalars(
                select(ChannelModel.id).where(
                    ChannelModel.pod_id == pod_id,
                    ChannelModel.kind == ChannelKind.CONTACT.value,
                    ChannelModel.contact_id.in_(ids),
                )
            )
        )

    async def _spoken_in(self, pod_id: UUID, ids: list[UUID]) -> set[UUID]:
        """The channels with a line they said, or one an agent said for them."""
        line = ChannelMessageModel
        return set(
            await self.session.scalars(
                select(line.channel_id)
                .where(
                    line.pod_id == pod_id,
                    line.sender_contact_id.in_(ids)
                    | line.on_behalf_contact_id.in_(ids),
                )
                .distinct()
            )
        )

    async def _seated_in(self, pod_id: UUID, ids: list[UUID]) -> set[UUID]:
        seat = ChannelMemberModel
        return set(
            await self.session.scalars(
                select(seat.channel_id)
                .where(seat.pod_id == pod_id, seat.contact_id.in_(ids))
                .distinct()
            )
        )

    async def _lock_one(self, channel_id: UUID) -> None:
        await self.session.execute(
            select(ChannelModel.id)
            .where(ChannelModel.id == channel_id)
            .with_for_update()
        )

    async def _empty_lines(self, channel_id: UUID, ids: list[UUID]) -> int:
        """Their lines keep their place, said by nobody, naming nobody; an
        agent's answer to them keeps its words and names nobody it was for.
        The channel is locked first, the order a new line takes it in."""
        line = ChannelMessageModel
        await self._lock_one(channel_id)
        said = await self.session.scalars(
            update(line)
            .where(line.channel_id == channel_id, line.sender_contact_id.in_(ids))
            .values(
                body="",
                parts=[],
                reply_ref=None,
                sender_contact_id=None,
                sender_name=None,
                external_message_id=None,
                deleted_at=func.coalesce(line.deleted_at, now()),
            )
            .returning(line.id)
            .execution_options(synchronize_session=False)
        )
        emptied = list(said)
        answered = await self.session.scalars(
            update(line)
            .where(line.channel_id == channel_id, line.on_behalf_contact_id.in_(ids))
            .values(on_behalf_contact_id=None)
            .returning(line.id)
            .execution_options(synchronize_session=False)
        )
        await drop_mentions(self.session, emptied)
        await _log_changed(self.session, channel_id, set(emptied) | set(answered))
        if emptied:
            d = ChannelMessageDeliveryModel
            await self.session.execute(
                update(d)
                .where(
                    d.message_id.in_(emptied),
                    d.status.in_(
                        (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                    ),
                    d.claimed_at.is_(None),
                )
                .values(
                    status=DeliveryStatus.SKIPPED.value,
                    reason_code=DeliveryReasonCode.FORGOTTEN.value,
                    reason=FORGOTTEN,
                    next_attempt_at=None,
                    updated_at=now(),
                )
                .execution_options(synchronize_session=False)
            )
        return len(emptied)


async def _log_changed(
    session: AsyncSession, channel_id: UUID, message_ids: Collection[UUID]
) -> None:
    """A change of the channel for each of these lines, in timeline order."""
    line = ChannelMessageModel
    await log_lines(
        session,
        channel_id,
        select(line.id).where(line.id.in_(sorted(message_ids))).order_by(line.seq),
    )


def _table_of(column: InstrumentedAttribute[UUID | None]) -> type[Base]:
    return cast("type[Base]", column.class_)


def _settle(
    head: ChannelModel, merged: ChannelModel, *, survivor_had_lines: bool
) -> None:
    """The survivor's channel answers by agent only if both let it, and shows
    the newer last line. Its cases are merged by ``CaseRepository.fold``."""
    head.agents_answer = head.agents_answer and merged.agents_answer
    if merged.last_line_id is not None and (
        not survivor_had_lines or merged.last_line_at > head.last_line_at
    ):
        head.last_line_id = merged.last_line_id
        head.last_line_at = merged.last_line_at


__all__ = [
    "DEDUPED_ON_MERGE",
    "FORGOTTEN",
    "RENAMED_ON_MERGE",
    "ContactChannels",
    "Fold",
]
