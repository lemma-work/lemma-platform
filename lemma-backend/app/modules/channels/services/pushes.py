"""Who is pushed a line outside the app, after it is written.

A line is posted under its channel's lock and committed; then
``ChannelMessagePosted`` runs ``fan_out`` (``events/push_work``), so a group
of any size costs the writer nothing. Every seat is read, a page at a time in
the order they joined, and each member who hears of the line has it folded
into their push window for the channel (``infrastructure/push_windows``):
what is due now goes as one delivery each, the rest when its window ends.

A member hears of a line, outside the app, for: a line naming them; a
question waiting on them; a line in their own channel they did not write; a
contact's line in a channel assigned to them (seated or not); and, set to ``ALL``, any line
written in Lemma in a group they are in. Never an agent's line at a platform
(seen there) or answering in a thread (seen in the app), nor a line withheld
from them (they could not read it). ``MENTIONS`` -- a group seat's default --
keeps only the first two, and so does a note for members only; ``NONE`` and a
mute stop them all. Somebody named or asked who is not in the channel is told
too, when they may see it. The first two *call* them: due at once, never held
by the window or the hourly budget, never held back while they have the app
open, and may go by email from any channel (``agent_surfaces``' sending).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, facts, pod_namespace
from app.modules.channels.contracts.facts import ChannelDeliveryDue
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Delivery,
    Member,
    NotifyLevel,
    SenderKind,
)
from app.modules.channels.domain.projection import withheld_from
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MAX_MEMBERS, MemberRepository
from app.modules.channels.infrastructure.models import ChannelMentionModel
from app.modules.channels.infrastructure.push_windows import Fold, Folded, PushWindows
from app.modules.channels.infrastructure.questions import QuestionRepository
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.services.viewers import member_sees

#: The work kind that sends a window once it ends.
CHANNELS_PUSH_WINDOW = "channels.push_window"


class PushWindowDue(BaseModel):
    """One member's push window of one channel has ended."""

    pod_id: UUID
    channel_id: UUID
    recipient_user_id: UUID


async def fan_out(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    message_id: UUID,
    assignee_user_id: UUID | None = None,
) -> None:
    """Fold a posted line into the push window of everybody who hears of it.
    ``assignee_user_id``: who had the contact's channel when the line was
    written -- whom a contact's line goes to, not whoever took it since."""
    channel = await ChannelRepository(uow.session).get(channel_id)
    line = await LineRepository(uow.session).get(message_id)
    if channel is None or line is None or line.is_deleted:
        return
    if channel.kind is ChannelKind.CONTACT:
        channel = channel.model_copy(update={"assignee_user_id": assignee_user_id})
    question = await QuestionRepository(uow.session).of_message(message_id)
    # A question put to a member is theirs to read, whoever's access made it.
    line = line.model_copy(update={"question": question})
    named = await _named(uow, message_id)
    awaited = question.awaits.user_id if question is not None else None
    called = named | ({awaited} if awaited else set())
    told = await _seated_who_hear(uow, channel, line, named=named, awaited=awaited)
    for user_id in await _called_not_seated(uow, channel, line, called):
        told[user_id] = True
    for user_id in await _assignee_not_seated(uow, channel, line):
        told.setdefault(user_id, False)
    await _fold(uow, channel, line, told)


async def push_named(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    message: ChannelMessage,
    user_ids: Collection[UUID],
) -> None:
    """Members a line now names (an edit adding them): told as anybody it
    named when written is -- unless they muted or silenced the channel, wrote
    it, or may not read it."""
    called = set(user_ids)
    if not called:
        return
    members = MemberRepository(uow.session)
    at = now()
    told: dict[UUID, bool] = {}
    for user_id in sorted(called - {message.sender.user_id}):
        seat = await members.of_user(channel.id, user_id)
        if _withheld(channel, message, user_id):
            continue
        if seat is not None and (seat.notify is NotifyLevel.NONE or _muted(seat, at)):
            continue
        if seat is not None or await member_sees(uow, channel, user_id):
            told[user_id] = True
    await _fold(uow, channel, message, told)


async def fold_back(
    uow: SqlAlchemyUnitOfWork, delivery: Delivery, *, hold_for: timedelta
) -> None:
    """A push the hourly budget held: its lines wait in the member's window
    of the channel, to go with the next push from it -- no sooner than
    ``hold_for`` unless a line calls them meanwhile."""
    if delivery.recipient_user_id is None:
        return
    channel = await ChannelRepository(uow.session).get(delivery.channel_id)
    if channel is None:
        return
    folded = await PushWindows(uow.session).fold(
        channel_id=channel.id,
        pod_id=channel.pod_id,
        folds=[
            Fold(
                user_id=delivery.recipient_user_id,
                urgent=False,
                from_seq=delivery.from_seq or delivery.seq,
                seq=delivery.seq,
                lines=delivery.lines,
            )
        ],
        not_before=hold_for,
    )
    await _send_or_schedule(uow, channel, folded)


async def send_window(uow: SqlAlchemyUnitOfWork, due: PushWindowDue) -> None:
    """A window ended: what it holds goes as one push, if it is due yet."""
    channel = await ChannelRepository(uow.session).get(due.channel_id)
    if channel is not None:
        await _send(uow, channel, [due.recipient_user_id])


# -- who hears ---------------------------------------------------------------------


async def _named(uow: SqlAlchemyUnitOfWork, message_id: UUID) -> set[UUID]:
    m = ChannelMentionModel
    rows = await uow.session.scalars(
        select(m.user_id)
        .where(m.message_id == message_id, m.user_id.is_not(None))
        .limit(MAX_MEMBERS)
    )
    return {row for row in rows if row is not None}


async def _seated_who_hear(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    message: ChannelMessage,
    *,
    named: set[UUID],
    awaited: UUID | None,
) -> dict[UUID, bool]:
    """Every seat of the channel that hears of the line, each with whether it
    calls them -- read a page at a time, however many there are."""
    members = MemberRepository(uow.session)
    at = now()
    heard: dict[UUID, bool] = {}
    after: tuple[datetime, UUID] | None = None
    while True:
        page = await members.people_to_tell(channel.id, after=after, limit=MAX_MEMBERS)
        for member in page:
            user_id = member.user_id
            if user_id is None or user_id == message.sender.user_id:
                continue
            if _muted(member, at) or _withheld(channel, message, user_id):
                continue
            if _hears(channel, message, member, named=named, awaited=awaited):
                heard[user_id] = user_id in named or user_id == awaited
        if len(page) < MAX_MEMBERS:
            return heard
        last = page[-1]
        assert last.joined_at is not None and last.seat_id is not None
        after = (last.joined_at, last.seat_id)


async def _called_not_seated(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    message: ChannelMessage,
    called: set[UUID],
) -> list[UUID]:
    """Who the line names or asks without being in the channel: told, when
    they may see it. A seated one was decided by their seat."""
    seated = await MemberRepository(uow.session).users_in(channel.id, called)
    told: list[UUID] = []
    for user_id in sorted(called - seated - {message.sender.user_id}):
        if user_id is None or _withheld(channel, message, user_id):
            continue
        if await member_sees(uow, channel, user_id):
            told.append(user_id)
    return told


async def _assignee_not_seated(
    uow: SqlAlchemyUnitOfWork, channel: Channel, message: ChannelMessage
) -> list[UUID]:
    """Who answers the contact, when the contact writes and they are not in
    the channel (they left it, or were assigned before an assignment seated
    them): told all the same, when they may still see it."""
    assignee = channel.assignee_user_id
    if (
        channel.kind is not ChannelKind.CONTACT
        or assignee is None
        or message.sender.kind is not SenderKind.CONTACT
        or message.members_only
    ):
        return []
    if await MemberRepository(uow.session).users_in(channel.id, {assignee}):
        return []
    if await member_sees(uow, channel, assignee):
        return [assignee]
    return []


def _muted(member: Member, at: datetime) -> bool:
    return member.muted_until is not None and member.muted_until > at


def _withheld(channel: Channel, message: ChannelMessage, user_id: UUID) -> bool:
    """A line they are shown only as given: never pushed to them in full."""
    return withheld_from(
        message,
        user_id=user_id,
        in_their_direct=channel.kind is ChannelKind.DIRECT
        and channel.member_user_id == user_id,
    )


def _hears(
    channel: Channel,
    message: ChannelMessage,
    member: Member,
    *,
    named: set[UUID],
    awaited: UUID | None,
) -> bool:
    """Whether this line reaches this member outside the app."""
    user_id = member.user_id
    if user_id in named or user_id == awaited:
        return True
    if member.notify is NotifyLevel.MENTIONS or message.members_only:
        # A note -- a notice, a member's aside -- waits in the app unless it
        # names them.
        return False
    if message.sender.kind is SenderKind.AGENT and (
        message.place_id is not None or message.thread_root_id is not None
    ):
        # Said at a platform, where it was seen; or an answer in the thread of
        # a line the member is writing in the app.
        return False
    if channel.kind is ChannelKind.DIRECT:
        return channel.member_user_id == user_id
    if channel.kind is ChannelKind.CONTACT:
        return (
            message.sender.kind is SenderKind.CONTACT
            and channel.assignee_user_id == user_id
        )
    # A group: a line written in Lemma, wherever it is carried. One heard on
    # a platform was seen there.
    return not message.heard_outside


# -- windows -------------------------------------------------------------------------


async def _fold(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    message: ChannelMessage,
    told: dict[UUID, bool],
) -> None:
    windows = PushWindows(uow.session)
    people = sorted(told)
    for start in range(0, len(people), MAX_MEMBERS):
        folded = await windows.fold(
            channel_id=channel.id,
            pod_id=channel.pod_id,
            folds=[
                Fold(
                    user_id=user_id,
                    urgent=told[user_id],
                    from_seq=message.seq,
                    seq=message.seq,
                )
                for user_id in people[start : start + MAX_MEMBERS]
            ],
        )
        await _send_or_schedule(uow, channel, folded)


async def _send_or_schedule(
    uow: SqlAlchemyUnitOfWork, channel: Channel, folded: list[Folded]
) -> None:
    """Send the windows that are due; set the rest to go when theirs end."""
    await _send(uow, channel, [each.user_id for each in folded if each.due])
    writer = WorkWriter.of(uow)
    for each in folded:
        if each.due:
            continue
        await writer.add(
            CHANNELS_PUSH_WINDOW,
            pod_namespace(channel.pod_id),
            PushWindowDue(
                pod_id=channel.pod_id,
                channel_id=channel.id,
                recipient_user_id=each.user_id,
            ),
            # One item per window end: lines folded meanwhile share it.
            dedup_key=(
                f"push-window:{channel.id}:{each.user_id}:"
                f"{each.next_attempt_at.timestamp():.0f}"
            ),
            run_at=each.next_attempt_at,
        )


async def _send(
    uow: SqlAlchemyUnitOfWork, channel: Channel, user_ids: list[UUID]
) -> None:
    windows = PushWindows(uow.session)
    taken = await windows.take(channel_id=channel.id, user_ids=user_ids)
    for delivery in await windows.plan_pushes(
        channel_id=channel.id, pod_id=channel.pod_id, windows=taken
    ):
        await facts.emit(
            uow,
            ChannelDeliveryDue(
                pod_id=delivery.pod_id,
                delivery_id=delivery.id,
                target=delivery.target_key,
            ),
        )


__all__ = [
    "CHANNELS_PUSH_WINDOW",
    "PushWindowDue",
    "fan_out",
    "fold_back",
    "push_named",
    "send_window",
]
