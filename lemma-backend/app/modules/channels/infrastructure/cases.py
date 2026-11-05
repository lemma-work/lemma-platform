"""A contact's cases: who answers them by hand, where it stands, whether it
asks for a person -- one episode at a time, every change recorded.

A contact's channel is made with its first case. A line written after its
current case closed opens the next one, which keeps whoever answered the last
by hand (the hand-back window still lets them go). A member reopening a closed
case by hand -- setting its status, or assigning somebody -- reopens that
same case. Every change appends a ``channel_case_events`` row, and a change
the channel's readers see is announced as the channel's (``touch_channel``).

Every writer holds the channel's row lock before the case's -- a post already
does, anything else takes it first -- so writers never wait on each other the
other way round, and two of them never open two cases. A post's own case write
is at most one statement per table (``after_line``): the channel row is the
post's to write, so a new case is named on it through ``set_on_head``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CompoundSelect, Select, and_, func, select, union_all, update
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
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import (
    ChannelCaseEventModel,
    ChannelCaseModel,
    ChannelModel,
)
from app.modules.channels.infrastructure.revisions import set_on_head, touch_channel
from app.modules.channels.infrastructure.rows import MAX_LINES, now

_OPEN = ContactStatus.OPEN.value
_CLOSED = ContactStatus.CLOSED.value


class CaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- a contact's channel is made with its first case -----------------------

    async def open_first(self, channel: ChannelModel) -> None:
        """The case a contact's channel starts with, named on the row."""
        case = self._new(channel.id, channel.pod_id, seq=channel.last_seq, at=now())
        await self.session.flush()
        channel.current_case_id = case.id
        set_committed_value(channel, "current_case", case)
        await self.session.flush()

    # -- a line ------------------------------------------------------------------

    async def after_line(self, channel: Channel, message: ChannelMessage) -> None:
        """A line in a contact's channel moves its case: a member writing to
        the contact steps in -- assigned if nobody is, their first response
        noted -- and waits on them, and their next wait is acknowledged afresh;
        the contact writing opens it again. A line after the case closed opens
        the next. Called by the post, which holds the channel's lock."""
        if channel.kind is not ChannelKind.CONTACT or message.members_only:
            return
        sender = message.sender
        member = sender.user_id if sender.kind is SenderKind.MEMBER else None
        if member is None and sender.kind is not SenderKind.CONTACT:
            return
        live = await self._live(channel.id, seq=message.seq - 1, at=message.created_at)
        if live is None:
            return
        case, shown = live
        if member is not None:
            shown = self._member_wrote(case, member, at=message.created_at) or shown
        else:
            shown = self._status(case, _OPEN, by=None) or shown
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
            shown = self._assign(case, user_id, by=user_id)
        if case.assignee_user_id == user_id and (
            case.assignee_last_line_at is None or at > case.assignee_last_line_at
        ):
            case.assignee_last_line_at = at
        if case.assignee_user_id == user_id and case.attention_at is not None:
            # Who answers them wrote: the wait for a reply is over.
            case.attention_at = None
            case.attention_reason = None
            shown = True
        if case.first_response_at is None:
            case.first_response_at = at
            case.first_response_by_user_id = user_id
            self._note(case, "first_response", by=user_id)
        if case.acknowledged_at is not None:
            case.acknowledged_at = None
        return (
            self._status(case, ContactStatus.WAITING_ON_CONTACT.value, by=user_id)
            or shown
        )

    # -- by hand, or by an agent ---------------------------------------------------

    async def assign(
        self, channel_id: UUID, user_id: UUID | None, *, by: UUID | None = None
    ) -> None:
        """Somebody answers this contact by hand now, or nobody does. Being
        assigned answers a call for a person, and opens the case."""
        await self._lock_channel(channel_id)
        if user_id is None:
            case = await self._current(channel_id)
            shown = case is not None and self._assign(case, None, by=by)
        else:
            case = await self._reopened(channel_id, by=by)
            if case is None:
                return
            shown = self._assign(case, user_id, by=by)
            shown = self._status(case, _OPEN, by=by) or shown
        await self.session.flush()
        if shown:
            await touch_channel(self.session, channel_id)

    async def set_status(
        self, channel_id: UUID, status: ContactStatus, *, by: UUID | None
    ) -> None:
        """Close the case, or set where an open one stands -- reopening it if
        it was closed."""
        await self._lock_channel(channel_id)
        if status is ContactStatus.CLOSED:
            case = await self._current(channel_id)
            shown = case is not None and self._close(case, by=by)
        else:
            case = await self._reopened(channel_id, by=by)
            shown = case is not None and self._status(case, status.value, by=by)
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
        self._note(case, "handed_back", user_id=str(user_id))
        await self.session.flush()
        await touch_channel(self.session, channel_id)
        return True

    async def acknowledge(self, channel_id: UUID) -> bool:
        """Claim the contact's one "a person will reply" of this wait -- until
        a member writes to them, or the case closes: whether this caller is
        the one to say it. Two messages arriving together say it once."""
        await self._lock_channel(channel_id)
        live = await self._live(channel_id, at=now())
        if live is None or live[0].acknowledged_at is not None:
            return False
        case, opened = live
        case.acknowledged_at = now()
        self._note(case, "acknowledged")
        await self.session.flush()
        if opened:
            await touch_channel(self.session, channel_id)
        return True

    async def call_for_person(self, channel_id: UUID, *, reason: str | None) -> bool:
        """The contact's case asks for a person, until somebody is assigned.
        Only a contact's channel has a case to ask in: whether this one had."""
        await self._lock_channel(channel_id)
        live = await self._live(channel_id, at=now())
        if live is None:
            return False
        case, shown = live
        if case.attention_at is None:
            case.attention_at = now()
            case.attention_reason = (reason or None) and reason[:255]
            self._note(case, "attention", reason=case.attention_reason)
            shown = True
        shown = self._status(case, _OPEN, by=None) or shown
        await self.session.flush()
        if shown:
            await touch_channel(self.session, channel_id)
        return True

    async def assigned_to(self, *, pod_id: UUID, user_id: UUID) -> list[UUID]:
        """The channels whose current case ``user_id`` answers."""
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
        """The merged channel's cases join the survivor's, both rows locked by
        the caller. Where both have an open case, the survivor's goes on: it
        takes the more recently assigned member, a call for a person either
        made, the earlier first response, and stays open if either was; the
        other is closed into it. Otherwise whichever is open -- or, neither,
        the more recently opened -- is the survivor's current case."""
        theirs = await self._for_update(merged.current_case_id)
        ours = await self._for_update(head.current_case_id)
        if _is_open(theirs) and _is_open(ours):
            assert theirs is not None and ours is not None
            _absorb(ours, theirs)
            self._close(theirs, by=None)
            self._note(ours, "merged", case_id=str(theirs.id))
        merged.current_case_id = None
        await self.session.flush()
        case = ChannelCaseModel
        await self.session.execute(
            update(case)
            .where(case.channel_id == merged.id)
            .values(channel_id=head.id)
            .execution_options(synchronize_session="fetch")
        )
        current = _current_of(ours, theirs)
        if current is not None and current is not ours:
            head.current_case_id = current.id
            set_committed_value(head, "current_case", current)
        await self.session.flush()
        await self._renumber(head.id)

    async def _renumber(self, channel_id: UUID) -> None:
        """A case opens after the line before it: counted again once the
        lines of both channels are numbered as one."""
        case, line = ChannelCaseModel, ChannelMessageModel
        before = (
            select(func.coalesce(func.max(line.seq), 0))
            .where(line.channel_id == channel_id, line.created_at < case.opened_at)
            .scalar_subquery()
        )
        await self.session.execute(
            update(case)
            .where(case.channel_id == channel_id)
            .values(opened_seq=before)
            .execution_options(synchronize_session="fetch")
        )

    # -- the case a writer works on ----------------------------------------------------

    async def _lock_channel(self, channel_id: UUID) -> None:
        await self.session.execute(
            select(ChannelModel.id)
            .where(ChannelModel.id == channel_id)
            .with_for_update()
        )

    async def _head(self, channel_id: UUID) -> tuple[UUID, int, UUID | None] | None:
        """A contact's channel's pod, newest line and current case."""
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

    async def _live(
        self, channel_id: UUID, *, at: datetime, seq: int | None = None
    ) -> tuple[ChannelCaseModel, bool] | None:
        """The channel's open case -- the one opened now, if its last had
        closed (``True``); ``None`` for a channel that is not a contact's."""
        head = await self._head(channel_id)
        if head is None:
            return None
        pod_id, last_seq, current_id = head
        current = await self._for_update(current_id)
        if current is not None and current.closed_at is None:
            return current, False
        case = self._new(
            channel_id,
            pod_id,
            seq=last_seq if seq is None else seq,
            at=at,
            after=current,
        )
        await self._name_current(channel_id, case)
        return case, True

    async def _reopened(
        self, channel_id: UUID, *, by: UUID | None
    ) -> ChannelCaseModel | None:
        """The current case, reopened if it was closed: a member acting on a
        closed case by hand takes it up again."""
        head = await self._head(channel_id)
        if head is None:
            return None
        pod_id, last_seq, current_id = head
        case = await self._for_update(current_id)
        if case is None:
            case = self._new(channel_id, pod_id, seq=last_seq, at=now())
            await self._name_current(channel_id, case)
        elif case.closed_at is not None:
            case.status = _OPEN
            case.closed_at = None
            case.closed_by_user_id = None
            self._note(case, "reopened", by=by)
        return case

    def _new(
        self,
        channel_id: UUID,
        pod_id: UUID,
        *,
        seq: int,
        at: datetime,
        after: ChannelCaseModel | None = None,
    ) -> ChannelCaseModel:
        """A new open case. One opened after another keeps who answered that
        one by hand: they hold the contact until the hand-back window lets
        them go."""
        case = ChannelCaseModel(
            id=uuid7(),
            channel_id=channel_id,
            pod_id=pod_id,
            opened_at=at,
            opened_seq=seq,
            status=_OPEN,
        )
        if after is not None and after.assignee_user_id is not None:
            case.assignee_user_id = after.assignee_user_id
            case.assigned_at = after.assigned_at
            case.assignee_last_line_at = after.assignee_last_line_at
        self.session.add(case)
        self._note(
            case,
            "opened",
            seq=seq,
            after=str(after.id) if after is not None else None,
            assignee=str(case.assignee_user_id) if case.assignee_user_id else None,
        )
        return case

    async def _name_current(self, channel_id: UUID, case: ChannelCaseModel) -> None:
        """The channel's current case is ``case``: in the post's own write to
        the row, when there is one. A row this session holds says so too."""
        await self.session.flush()
        await set_on_head(self.session, channel_id, current_case_id=case.id)
        row = self.session.identity_map.get(identity_key(ChannelModel, channel_id))
        if isinstance(row, ChannelModel):
            set_committed_value(row, "current_case_id", case.id)
            set_committed_value(row, "current_case", case)

    # -- what changes, and its record ----------------------------------------------------

    def _assign(
        self, case: ChannelCaseModel, user_id: UUID | None, *, by: UUID | None
    ) -> bool:
        if case.assignee_user_id == user_id:
            return False
        case.assignee_user_id = user_id
        case.assigned_at = now() if user_id is not None else None
        case.assignee_last_line_at = None
        if user_id is not None:
            case.attention_at = None
            case.attention_reason = None
        self._note(case, "assigned", by=by, user_id=str(user_id) if user_id else None)
        return True

    def _status(self, case: ChannelCaseModel, status: str, *, by: UUID | None) -> bool:
        if case.status == status:
            return False
        self._note(case, "status", by=by, status=status, was=case.status)
        case.status = status
        return True

    def _close(self, case: ChannelCaseModel, *, by: UUID | None) -> bool:
        if case.closed_at is not None:
            return False
        case.status = _CLOSED
        case.closed_at = now()
        case.closed_by_user_id = by
        case.acknowledged_at = None
        self._note(case, "closed", by=by)
        return True

    def _note(
        self,
        case: ChannelCaseModel,
        kind: str,
        *,
        by: UUID | None = None,
        **value: object,
    ) -> None:
        shown = {key: each for key, each in value.items() if each is not None}
        self.session.add(
            ChannelCaseEventModel(
                case=case, kind=kind, actor_user_id=by, value=shown or None
            )
        )


def narrowed_by_case(
    statement: Select[tuple[ChannelModel]],
    *,
    pod_id: UUID,
    assignee_user_id: UUID | None = None,
    unassigned: bool = False,
    status: ContactStatus | None = None,
    needs_person: bool = False,
) -> Select[tuple[ChannelModel]]:
    """A channel list narrowed to contacts by their current case: who answers
    it, where it stands, or -- ``needs_person`` -- waiting on a person: open,
    nobody on it, and either it asked for one or the agent is off."""
    channel, case = ChannelModel, ChannelCaseModel
    contact = channel.kind == ChannelKind.CONTACT.value
    if needs_person:
        statement = statement.where(
            contact, channel.id.in_(_waiting_on_a_person(pod_id))
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


def _waiting_on_a_person(pod_id: UUID) -> CompoundSelect:
    """The pod's contacts' channels waiting on a person, from two small
    indexes: open cases that asked for one (``ix_channel_cases_needs_person``),
    and channels whose agents are off (``ix_channels_agents_off``) with an
    open case nobody holds."""
    channel, case = ChannelModel, ChannelCaseModel
    asked = select(case.channel_id).where(
        case.pod_id == pod_id,
        case.status == _OPEN,
        case.assignee_user_id.is_(None),
        case.attention_at.is_not(None),
    )
    agents_off = (
        select(channel.id)
        .join(case, case.id == channel.current_case_id)
        .where(
            channel.pod_id == pod_id,
            channel.kind == ChannelKind.CONTACT.value,
            ~channel.agents_answer,
            case.status == _OPEN,
            case.assignee_user_id.is_(None),
        )
    )
    return union_all(asked, agents_off)


def _is_open(case: ChannelCaseModel | None) -> bool:
    return case is not None and case.closed_at is None


def _absorb(ours: ChannelCaseModel, theirs: ChannelCaseModel) -> None:
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
    if ours.attention_at is None and theirs.attention_at is not None:
        ours.attention_at = theirs.attention_at
        ours.attention_reason = theirs.attention_reason
    if theirs.status == _OPEN:
        ours.status = _OPEN
    if ours.acknowledged_at is None:
        ours.acknowledged_at = theirs.acknowledged_at
    if theirs.first_response_at is not None and (
        ours.first_response_at is None
        or theirs.first_response_at < ours.first_response_at
    ):
        ours.first_response_at = theirs.first_response_at
        ours.first_response_by_user_id = theirs.first_response_by_user_id


def _current_of(
    ours: ChannelCaseModel | None, theirs: ChannelCaseModel | None
) -> ChannelCaseModel | None:
    """After a fold: the open case, else the more recently opened."""
    for case in (ours, theirs):
        if _is_open(case):
            return case
    cases = [case for case in (ours, theirs) if case is not None]
    return max(cases, key=lambda case: case.opened_at) if cases else None


__all__ = ["CaseRepository"]
