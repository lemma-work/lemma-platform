"""A contact's case: who answers them by hand, where it stands, whether it
asks for a person -- one row, as it is now.

A contact's channel is made with its case (``channels.current_case_id``) and
keeps it: closing sets its status, and anything that moves a closed case --
the contact or a member writing, a member assigning somebody or setting where
it stands, a run asking for a person -- opens that same row again, still held
by whoever answered it by hand (the hand-back window still lets them go).
Nothing is kept of how it got there. A change the channel's readers see is
announced as the channel's (``touch_channel``).

Every writer holds the channel's row lock before the case's -- a post already
does, anything else takes it first -- so writers never wait on each other the
other way round. A post's own case write is at most one statement per table
(``after_line``): the channel row is the post's to write, so a case made for a
channel that had none is named on it through ``set_on_head``.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CompoundSelect, Select, and_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.util import identity_key
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    ContactStatus,
    SenderKind,
)
from app.modules.channels.infrastructure.models import ChannelCaseModel, ChannelModel
from app.modules.channels.infrastructure.revisions import set_on_head, touch_channel
from app.modules.channels.infrastructure.rows import MAX_LINES, now

_OPEN = ContactStatus.OPEN.value
_CLOSED = ContactStatus.CLOSED.value


class CaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- a contact's channel is made with its case ------------------------------

    async def open_first(self, channel: ChannelModel) -> None:
        """The case a contact's channel is made with, named on the row."""
        case = self._new(channel.id, channel.pod_id, seq=channel.last_seq, at=now())
        await self.session.flush()
        channel.current_case_id = case.id
        set_committed_value(channel, "current_case", case)
        await self.session.flush()

    # -- a line ------------------------------------------------------------------

    async def after_line(self, channel: Channel, message: ChannelMessage) -> None:
        """A line in a contact's channel moves its case: a member writing to
        the contact steps in -- assigned if nobody is -- and waits on them, and
        their next wait is acknowledged afresh; the contact writing opens it
        again. Called by the post, which holds the channel's lock."""
        if channel.kind is not ChannelKind.CONTACT or message.members_only:
            return
        sender = message.sender
        member = sender.user_id if sender.kind is SenderKind.MEMBER else None
        if member is None and sender.kind is not SenderKind.CONTACT:
            return
        live = await self._open_case(channel.id)
        if live is None:
            return
        case, shown = live
        if member is not None:
            shown = self._member_wrote(case, member, at=message.created_at) or shown
        else:
            shown = self._status(case, _OPEN) or shown
        await self.session.flush()
        if shown:
            await touch_channel(self.session, channel.id)

    def _member_wrote(
        self, case: ChannelCaseModel, user_id: UUID, *, at: datetime
    ) -> bool:
        shown = False
        if case.assignee_user_id is None:
            # Of two members writing to an unanswered contact at once, the
            # first takes it: the post holds the channel's lock.
            shown = self._assign(case, user_id)
        if case.assignee_user_id == user_id and (
            case.assignee_last_line_at is None or at > case.assignee_last_line_at
        ):
            case.assignee_last_line_at = at
        if case.assignee_user_id == user_id and case.attention_at is not None:
            # Who answers them wrote: the wait for a reply is over.
            case.attention_at = None
            case.attention_reason = None
            shown = True
        if case.acknowledged_at is not None:
            case.acknowledged_at = None
        return self._status(case, ContactStatus.WAITING_ON_CONTACT.value) or shown

    # -- by hand, or by an agent ---------------------------------------------------

    async def assign(self, channel_id: UUID, user_id: UUID | None) -> None:
        """Somebody answers this contact by hand now, or nobody does. Being
        assigned answers a call for a person, and opens the case."""
        await self._lock_channel(channel_id)
        if user_id is None:
            case = await self._current(channel_id)
            shown = case is not None and self._assign(case, None)
        else:
            live = await self._open_case(channel_id)
            if live is None:
                return
            case, shown = live
            shown = self._assign(case, user_id) or shown
            shown = self._status(case, _OPEN) or shown
        await self.session.flush()
        if shown:
            await touch_channel(self.session, channel_id)

    async def set_status(self, channel_id: UUID, status: ContactStatus) -> None:
        """Close the case, or set where an open one stands -- opening it again
        if it was closed."""
        await self._lock_channel(channel_id)
        if status is ContactStatus.CLOSED:
            case = await self._current(channel_id)
            shown = case is not None and self._close(case)
        else:
            live = await self._open_case(channel_id)
            shown = live is not None and (
                self._status(live[0], status.value) or live[1]
            )
        await self.session.flush()
        if shown:
            await touch_channel(self.session, channel_id)

    async def hand_back(
        self, channel_id: UUID, *, user_id: UUID, quiet_since: datetime
    ) -> bool:
        """Let go of an assignee who has not written since ``quiet_since``
        (counted from being assigned until they first write): a member
        writing meanwhile keeps it, and of two turns letting go at once only
        one does -- both hold the channel's lock. Whether this one did."""
        await self._lock_channel(channel_id)
        case = await self._current(channel_id)
        if case is None or case.assignee_user_id != user_id:
            return False
        last = case.assignee_last_line_at or case.assigned_at
        if last is None or last >= quiet_since:
            return False
        case.assignee_user_id = None
        case.assigned_at = None
        case.assignee_last_line_at = None
        # The agent answers them again: nobody is waited on.
        case.attention_at = None
        case.attention_reason = None
        await self.session.flush()
        await touch_channel(self.session, channel_id)
        return True

    async def acknowledge(self, channel_id: UUID) -> bool:
        """Claim the contact's one "a person will reply" of this wait -- until
        a member writes to them, or the case closes: whether this caller is
        the one to say it. Two messages arriving together say it once."""
        await self._lock_channel(channel_id)
        live = await self._open_case(channel_id)
        if live is None or live[0].acknowledged_at is not None:
            return False
        case, opened = live
        case.acknowledged_at = now()
        await self.session.flush()
        if opened:
            await touch_channel(self.session, channel_id)
        return True

    async def call_for_person(self, channel_id: UUID, *, reason: str | None) -> bool:
        """The contact's case asks for a person, until somebody is assigned.
        Only a contact's channel has a case to ask in: whether this one had."""
        await self._lock_channel(channel_id)
        live = await self._open_case(channel_id)
        if live is None:
            return False
        case, shown = live
        if case.attention_at is None:
            case.attention_at = now()
            case.attention_reason = (reason or None) and reason[:255]
            shown = True
        shown = self._status(case, _OPEN) or shown
        await self.session.flush()
        if shown:
            await touch_channel(self.session, channel_id)
        return True

    async def assigned_to(self, *, pod_id: UUID, user_id: UUID) -> list[UUID]:
        """The channels whose case ``user_id`` answers."""
        case, channel = ChannelCaseModel, ChannelModel
        return list(
            await self.session.scalars(
                select(channel.id)
                .join(case, case.id == channel.current_case_id)
                .where(case.pod_id == pod_id, case.assignee_user_id == user_id)
                .limit(MAX_LINES)
            )
        )

    # -- a contact who turned out to be another ------------------------------------

    async def fold(self, merged: ChannelModel, head: ChannelModel) -> None:
        """The merged channel's case into the survivor's, both rows locked by
        the caller: the survivor keeps its one case, taking the more recently
        assigned member and -- where the merged one was open -- its call for
        a person and its being open. The merged case goes with its channel;
        a survivor with no case of its own takes it instead."""
        theirs = await self._for_update(merged.current_case_id)
        if theirs is None:
            return
        ours = await self._for_update(head.current_case_id)
        if ours is not None:
            _absorb(ours, theirs)
            await self.session.flush()
            return
        merged.current_case_id = None
        await self.session.flush()
        theirs.channel_id = head.id
        await self.session.flush()
        head.current_case_id = theirs.id
        set_committed_value(head, "current_case", theirs)
        await self.session.flush()

    # -- the case a writer works on ----------------------------------------------------

    async def _lock_channel(self, channel_id: UUID) -> None:
        await self.session.execute(
            select(ChannelModel.id)
            .where(ChannelModel.id == channel_id)
            .with_for_update()
        )

    async def _head(self, channel_id: UUID) -> tuple[UUID, int, UUID | None] | None:
        """A contact's channel's pod, newest line and case."""
        channel = ChannelModel
        row = (
            await self.session.execute(
                select(channel.pod_id, channel.last_seq, channel.current_case_id).where(
                    channel.id == channel_id, channel.kind == ChannelKind.CONTACT.value
                )
            )
        ).first()
        return (row[0], int(row[1]), row[2]) if row is not None else None

    async def _for_update(self, case_id: UUID | None) -> ChannelCaseModel | None:
        if case_id is None:
            return None
        return await self.session.scalar(
            select(ChannelCaseModel)
            .where(ChannelCaseModel.id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def _current(self, channel_id: UUID) -> ChannelCaseModel | None:
        head = await self._head(channel_id)
        return await self._for_update(head[2]) if head is not None else None

    async def _open_case(
        self, channel_id: UUID
    ) -> tuple[ChannelCaseModel, bool] | None:
        """The channel's case, open: opened again if it was closed (``True``),
        made if the channel had none; ``None`` for a channel that is not a
        contact's.

        Opened again, it starts a new wait: no call for a person, nothing told
        the contact yet. ``_close`` clears both, but a case closed before it
        did -- when the next line opened a new case instead -- still holds
        them."""
        head = await self._head(channel_id)
        if head is None:
            return None
        pod_id, last_seq, case_id = head
        case = await self._for_update(case_id)
        if case is None:
            case = self._new(channel_id, pod_id, seq=last_seq, at=now())
            await self._name_current(channel_id, case)
            return case, True
        if case.closed_at is None:
            return case, False
        case.status = _OPEN
        case.closed_at = None
        case.attention_at = None
        case.attention_reason = None
        case.acknowledged_at = None
        return case, True

    def _new(
        self, channel_id: UUID, pod_id: UUID, *, seq: int, at: datetime
    ) -> ChannelCaseModel:
        """A contact's case, open and held by nobody."""
        case = ChannelCaseModel(
            id=uuid7(),
            channel_id=channel_id,
            pod_id=pod_id,
            opened_at=at,
            opened_seq=seq,
            status=_OPEN,
        )
        self.session.add(case)
        return case

    async def _name_current(self, channel_id: UUID, case: ChannelCaseModel) -> None:
        """The channel's case is ``case``: in the post's own write to the row,
        when there is one. A row this session holds says so too."""
        await self.session.flush()
        await set_on_head(self.session, channel_id, current_case_id=case.id)
        row = self.session.identity_map.get(identity_key(ChannelModel, channel_id))
        if isinstance(row, ChannelModel):
            set_committed_value(row, "current_case_id", case.id)
            set_committed_value(row, "current_case", case)

    # -- what changes ---------------------------------------------------------------------

    def _assign(self, case: ChannelCaseModel, user_id: UUID | None) -> bool:
        if case.assignee_user_id == user_id:
            return False
        case.assignee_user_id = user_id
        case.assigned_at = now() if user_id is not None else None
        case.assignee_last_line_at = None
        if user_id is not None:
            case.attention_at = None
            case.attention_reason = None
        return True

    def _status(self, case: ChannelCaseModel, status: str) -> bool:
        if case.status == status:
            return False
        case.status = status
        return True

    def _close(self, case: ChannelCaseModel) -> bool:
        """Closed, its wait over: opened again, it asks for nobody and has
        told the contact nothing yet. Who answered it by hand stays."""
        if case.closed_at is not None:
            return False
        case.status = _CLOSED
        case.closed_at = now()
        case.attention_at = None
        case.attention_reason = None
        case.acknowledged_at = None
        return True


def narrowed_by_case(
    statement: Select[tuple[ChannelModel]],
    *,
    pod_id: UUID,
    assignee_user_id: UUID | None = None,
    unassigned: bool = False,
    status: ContactStatus | None = None,
    needs_person: bool = False,
) -> Select[tuple[ChannelModel]]:
    """A channel list narrowed to contacts by their case: who answers it,
    where it stands, or -- ``needs_person`` -- waiting on a person: open,
    nobody on it, and either it asked for one or the agent is off."""
    channel, case = ChannelModel, ChannelCaseModel
    contact = channel.kind == ChannelKind.CONTACT.value
    if needs_person:
        statement = statement.where(
            contact, channel.id.in_(waiting_on_a_person([pod_id]))
        )
    if assignee_user_id is None and not unassigned and status is None:
        return statement
    statement = statement.join(
        case, and_(case.id == channel.current_case_id, case.channel_id == channel.id)
    ).where(contact)
    if assignee_user_id is not None:
        statement = statement.where(case.assignee_user_id == assignee_user_id)
    if unassigned:
        statement = statement.where(case.assignee_user_id.is_(None))
    if status is not None:
        statement = statement.where(case.status == status.value)
    return statement


def waiting_on_a_person(pod_ids: Collection[UUID]) -> CompoundSelect:
    """These pods' contacts' channels waiting on a person, from two small
    indexes: open cases that asked for one (``ix_channel_cases_needs_person``),
    and channels whose agents are off (``ix_channels_agents_off``) with an
    open case nobody holds. One pod for a pod's list, several for a list
    across an organization -- the same rule either way."""
    channel, case = ChannelModel, ChannelCaseModel
    pods = list(pod_ids)
    asked = select(case.channel_id).where(
        case.pod_id.in_(pods),
        case.status == _OPEN,
        case.assignee_user_id.is_(None),
        case.attention_at.is_not(None),
    )
    agents_off = (
        select(channel.id)
        .join(case, case.id == channel.current_case_id)
        .where(
            channel.pod_id.in_(pods),
            channel.kind == ChannelKind.CONTACT.value,
            ~channel.agents_answer,
            case.status == _OPEN,
            case.assignee_user_id.is_(None),
        )
    )
    return union_all(asked, agents_off)


def _is_open(case: ChannelCaseModel) -> bool:
    return case.closed_at is None


def _absorb(ours: ChannelCaseModel, theirs: ChannelCaseModel) -> None:
    """The merged contact's case into the survivor's."""
    if theirs.assignee_user_id is not None and (
        ours.assignee_user_id is None
        or (
            theirs.assigned_at is not None
            and ours.assigned_at is not None
            and theirs.assigned_at > ours.assigned_at
        )
    ):
        ours.assignee_user_id = theirs.assignee_user_id
        ours.assigned_at = theirs.assigned_at
        ours.assignee_last_line_at = theirs.assignee_last_line_at
    if not _is_open(theirs):
        return
    if not _is_open(ours):
        # Theirs is the one going on: where it stands, and its wait.
        ours.status, ours.closed_at = theirs.status, None
        ours.attention_at = theirs.attention_at
        ours.attention_reason = theirs.attention_reason
        ours.acknowledged_at = theirs.acknowledged_at
        return
    if ours.attention_at is None and theirs.attention_at is not None:
        ours.attention_at = theirs.attention_at
        ours.attention_reason = theirs.attention_reason
    if theirs.status == _OPEN:
        ours.status = _OPEN
    if ours.acknowledged_at is None:
        ours.acknowledged_at = theirs.acknowledged_at


__all__ = ["CaseRepository"]
