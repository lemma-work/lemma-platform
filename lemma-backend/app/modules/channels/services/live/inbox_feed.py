"""The ``inbox`` topic's half of a pod's hub: whose counts moved, and to what.

The inbox is not a log to replay; it is counts, worked out again from the
tables whenever something they count may have moved -- a channel's change
counter (a line, a question, a seat) or a reader's read position. The feed
remembers every channel's head as it last saw it, so on a signal it reads only
the channels named, on a poll only the channels changed since the last
(``events.changed_heads``), and only after signals may have been missed every
channel of the pod, in one query, to find the ones that moved. Its heads move
once every reader has been told, so a read that fails part way is made again.

Each event is absolute -- the channel's counts and the totals now -- so a
client applies whichever came last; a subscriber that resumes is told to
refetch ``/inbox`` instead (``resync``).

A reader's counts are worked out together for every channel that moved for
them: a fixed number of reads per reader however many channels moved, and
which of those channels each reader is in is one read for every reader. The
feed also remembers which channels each reader may see, so one that stops
being theirs -- taken out of a private group, the grant gone, the channel
deleted -- is sent once more with nothing in it, and its badge clears; a
channel they never saw is never named to them.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.protocol import ResyncReason, UnsubscribedReason
from app.modules.channels.api.access import Caller
from app.modules.channels.domain.access import may_see
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.inbox import InboxReads, Totals, Unread
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.services.live.events import (
    Head,
    LinesView,
    changed_heads,
    heads_of,
    inbox_updated,
    lines_view,
    pod_heads,
    question_waiting,
)
from app.modules.channels.services.live.readers import caller_for
from app.modules.channels.services.live.subscriptions import InboxSub

#: New questions one channel's change is searched for: past it, the reader's
#: ``waiting`` count still moves; only the ``question.waiting`` events stop.
_QUESTION_SCAN = 200
#: The channels a reader is taken to have seen when they subscribe: the most
#: recently active this many. One quieter than all of them that they then lose
#: sight of is not cleared over the socket; their next ``/inbox`` read clears it.
SEEN_AT_JOIN = 2000


class InboxFeed:
    def __init__(self, pod_id: UUID) -> None:
        self.pod_id = pod_id
        #: Subscriptions by reader.
        self.subs: dict[UUID, set[InboxSub]] = {}
        #: Every channel of the pod as last read; ``None`` before the first.
        self._heads: dict[UUID, Head] | None = None
        #: memory: per reader, the pod's channels they may see -- from
        #: ``SEEN_AT_JOIN`` at first, plus those sent to them since; dropped
        #: with the reader's last subscription.
        self._seen: dict[UUID, set[UUID]] = {}
        #: memory: per reader, what they were last told: each channel's
        #: counts, and their totals. A channel whose counts did not move is
        #: not sent again; one never sent was nothing to them (``Unread()``).
        #: Dropped with the reader's last subscription.
        self._told: dict[UUID, _Told] = {}

    def __bool__(self) -> bool:
        return bool(self.subs)

    @property
    def users(self) -> set[UUID]:
        return set(self.subs)

    async def join(self, uow: SqlAlchemyUnitOfWork, sub: InboxSub) -> None:
        """Index ``sub``. The first reader reads every head, so the next change
        is measured from now. Runs under the hub's lock."""
        if self._heads is None:
            self._heads = await pod_heads(uow, self.pod_id)
        if sub.user_id not in self._seen:
            self._seen[sub.user_id] = await _seen_by(uow, sub.caller)
        if sub.user_id not in self._told:
            # The totals they fetched with ``/inbox``, near enough: a first
            # change that leaves them as they are is not sent.
            self._told[sub.user_id] = _Told(
                totals=await InboxReads(uow.session).totals(sub.caller.viewer)
            )
        self.subs.setdefault(sub.user_id, set()).add(sub)

    def release(self, sub: InboxSub) -> None:
        subs = self.subs.get(sub.user_id)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            self.subs.pop(sub.user_id, None)
            self._seen.pop(sub.user_id, None)
            self._told.pop(sub.user_id, None)
        if not self.subs:
            self._heads = None

    async def drain(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        dirty: Collection[UUID],
        reads: Collection[tuple[UUID, UUID]],
        everything: bool,
        changed_since: datetime | None = None,
    ) -> None:
        """Tell each reader about the channels whose counts may have moved for
        them: ``dirty``, those changed since ``changed_since`` (a poll), or
        with ``everything`` every channel of the pod.

        The feed's heads and what each reader was shown move only once every
        reader has been told: a read that fails part way leaves both as they
        were, and the next read tells everyone again."""
        if not self.subs or self._heads is None:
            return
        moved, now, gone = await self._moved(
            uow, dirty, everything=everything, changed_since=changed_since
        )
        for_user = self._readers_of(moved, reads)
        shown: dict[UUID, _Shown] = {}
        if for_user:
            wanted = {channel_id for ids in for_user.values() for channel_id in ids}
            channels = await ChannelRepository(uow.session).by_ids(wanted)
            views = await _new_lines(uow, moved, channels)
            joined = await _memberships(uow, set(for_user), set(channels))
            for user_id, channel_ids in for_user.items():
                shown[user_id] = await self._tell(
                    uow,
                    user_id,
                    channel_ids,
                    channels,
                    views,
                    joined.get(user_id, set()),
                    reshaped={
                        channel_id
                        for channel_id, (before, after) in moved.items()
                        if after.changed_rev > before.changed_rev
                    },
                )
        # Everyone told: only now do the heads and what each reader saw move.
        self._heads.update(now)
        for channel_id in gone:
            self._heads.pop(channel_id, None)
        for user_id, told in shown.items():
            seen = self._seen.get(user_id)
            if seen is not None:
                seen |= told.added
                seen -= told.lost
            if user_id in self.subs:
                memory = self._told.setdefault(user_id, _Told())
                memory.unread.update(told.unread)
                for channel_id in told.lost:
                    memory.unread.pop(channel_id, None)
                if told.totals is not None:
                    memory.totals = told.totals

    def _readers_of(
        self,
        moved: Collection[UUID],
        reads: Collection[tuple[UUID, UUID]],
    ) -> dict[UUID, set[UUID]]:
        """Per reader, the channels whose counts may have moved for them."""
        for_user: dict[UUID, set[UUID]] = {
            user_id: set(moved) for user_id in self.subs if moved
        }
        for channel_id, user_id in reads:
            if user_id in self.subs:
                for_user.setdefault(user_id, set()).add(channel_id)
        return for_user

    async def _moved(
        self,
        uow: SqlAlchemyUnitOfWork,
        dirty: Collection[UUID],
        *,
        everything: bool,
        changed_since: datetime | None,
    ) -> tuple[dict[UUID, tuple[Head, Head]], dict[UUID, Head], set[UUID]]:
        """What moved since the feed's heads, the heads read, and the
        channels found gone; nothing of the feed's own is changed."""
        assert self._heads is not None
        if everything:
            now = await pod_heads(uow, self.pod_id)
        else:
            now = await heads_of(uow, dirty)
            if changed_since is not None:
                now.update(await changed_heads(uow, self.pod_id, changed_since))
        moved: dict[UUID, tuple[Head, Head]] = {}
        for channel_id, head in now.items():
            before = self._heads.get(channel_id, Head(0, 0))
            if head.rev > before.rev:
                moved[channel_id] = (before, head)
        # A channel deleted: its readers' badges clear.
        looked_at = set(self._heads) if everything else set(dirty)
        gone = {
            channel_id
            for channel_id in looked_at - set(now)
            if channel_id in self._heads
        }
        for channel_id in gone:
            before = self._heads[channel_id]
            moved[channel_id] = (before, before)
        return moved, now, gone

    async def _tell(
        self,
        uow: SqlAlchemyUnitOfWork,
        user_id: UUID,
        channel_ids: set[UUID],
        channels: dict[UUID, Channel],
        views: dict[UUID, LinesView],
        joined: set[UUID],
        *,
        reshaped: Collection[UUID],
    ) -> _Shown:
        """Offer the reader what moved for them; what they were shown, for
        the caller to remember once everyone is told.

        Only numbers that moved are sent. A channel whose counts are as the
        reader was last told -- a line in a group they are not in, say --
        costs them no totals read and no frame, unless the channel itself
        changed (``reshaped``: archived, its members or settings), which can
        move their totals alone."""
        subs = [sub for sub in self.subs.get(user_id, ()) if sub.active]
        if not subs:
            return _Shown()
        caller = subs[0].caller
        shown, lost = self._in_sight(user_id, caller, channel_ids, channels, joined)
        if not shown and not lost:
            return _Shown()
        added = {channel.id for channel in shown}
        told = self._told.get(user_id, _Told())
        inbox = InboxReads(uow.session)
        unread = await inbox.unread(caller.viewer, shown)
        moved = _moved_counts(unread, told.unread, added)
        questions = _questions_for(views, shown, user_id)
        news = bool(moved or lost or questions)
        if not news and not added & set(reshaped):
            return _Shown(added=added)
        totals = await inbox.totals(caller.viewer)
        if not news and totals == told.totals:
            return _Shown(added=added)
        if not moved and not lost:
            # Only the totals moved: they ride one channel's event.
            moved = _moved_counts(unread, {}, [shown[0].id], every=True)
        events = _events(shown, lost, set(moved), unread, totals, questions)
        for sub in subs:
            sub.connection.offer_events(sub.id, None, events)
        return _Shown(added=added, lost=set(lost), unread=moved, totals=totals)

    def _in_sight(
        self,
        user_id: UUID,
        caller: Caller,
        channel_ids: set[UUID],
        channels: dict[UUID, Channel],
        joined: set[UUID],
    ) -> tuple[list[Channel], list[UUID]]:
        """Of the channels that moved, those the reader may see, and those
        they saw and no longer may."""
        seen = self._seen.get(user_id, set())
        shown: list[Channel] = []
        lost: list[UUID] = []
        for channel_id in sorted(channel_ids):
            channel = channels.get(channel_id)
            if channel is not None and may_see(
                channel, caller.viewer, is_member=channel_id in joined
            ):
                shown.append(channel)
            elif channel_id in seen:
                lost.append(channel_id)
        return shown, lost

    async def recheck(
        self, uow: SqlAlchemyUnitOfWork, end: Callable[[InboxSub, str], None]
    ) -> None:
        """Rebuild each reader: end those who may no longer read channels, and
        have those whose access changed refetch their counts. A reader whose
        access is as it was is left alone -- a re-check on the clock, or a
        grant elsewhere in the pod, costs them nothing."""
        for user_id, subs in list(self.subs.items()):
            caller = await caller_for(uow, pod_id=self.pod_id, user_id=user_id)
            if caller is None:
                for sub in list(subs):
                    end(sub, UnsubscribedReason.ACCESS_REVOKED.value)
                continue
            changed = [sub for sub in subs if not _same(sub.caller, caller)]
            if not changed:
                continue
            self._seen[user_id] = await _seen_by(uow, caller)
            for sub in subs:
                sub.caller = caller
            for sub in changed:
                sub.connection.offer_resync(
                    sub.id, None, ResyncReason.PERMISSION_CHANGED
                )


def _moved_counts(
    now: Mapping[UUID, Unread],
    before: Mapping[UUID, Unread],
    channel_ids: Collection[UUID],
    *,
    every: bool = False,
) -> dict[UUID, Unread]:
    """Of ``channel_ids``, those whose counts are not as they were (with
    ``every``, all of them), with their counts now; absent is ``Unread()``."""
    return {
        channel_id: now.get(channel_id, Unread())
        for channel_id in channel_ids
        if every or now.get(channel_id, Unread()) != before.get(channel_id, Unread())
    }


def _questions_for(
    views: Mapping[UUID, LinesView], shown: list[Channel], reader_id: UUID
) -> dict[UUID, list[str]]:
    """Per channel, the questions newly waiting on the reader."""
    found = {
        channel.id: question_waiting(views[channel.id], reader_id)
        for channel in shown
        if channel.id in views
    }
    return {channel_id: events for channel_id, events in found.items() if events}


def _events(
    shown: list[Channel],
    lost: list[UUID],
    sent: set[UUID],
    unread: Mapping[UUID, Unread],
    totals: Totals,
    questions: Mapping[UUID, list[str]],
) -> list[str]:
    """The reader's events: the counts of ``sent``, the questions newly
    waiting on them, and what they can no longer see counted as nothing --
    its badge clears."""
    events: list[str] = []
    for channel in shown:
        if channel.id in sent:
            events.append(inbox_updated(channel.id, unread.get(channel.id), totals))
        events.extend(questions.get(channel.id, ()))
    events.extend(inbox_updated(channel_id, None, totals) for channel_id in lost)
    return events


@dataclass(slots=True)
class _Told:
    """What one reader was last told."""

    unread: dict[UUID, Unread] = field(default_factory=dict)
    totals: Totals | None = None


@dataclass(frozen=True, slots=True)
class _Shown:
    """What one drain told one reader."""

    added: set[UUID] = field(default_factory=set)
    lost: set[UUID] = field(default_factory=set)
    unread: dict[UUID, Unread] = field(default_factory=dict)
    totals: Totals | None = None


def _same(before: Caller, after: Caller) -> bool:
    return (
        before.viewer == after.viewer
        and before.messages_contacts == after.messages_contacts
        and before.manages_channels == after.manages_channels
    )


async def _new_lines(
    uow: SqlAlchemyUnitOfWork,
    moved: Mapping[UUID, tuple[Head, Head]],
    channels: Mapping[UUID, Channel],
) -> dict[UUID, LinesView]:
    """The lines new in each moved channel, named once for every reader: where
    a question newly waiting on one of them is found."""
    views: dict[UUID, LinesView] = {}
    for channel_id, (before, after) in moved.items():
        channel = channels.get(channel_id)
        if channel is not None and after.seq > before.seq:
            # Only the new lines asking a question: a burst of chatter around
            # one does not push it out of the scan.
            lines = await LineRepository(uow.session).changed_since(
                channel_id,
                after_rev=before.rev,
                limit=_QUESTION_SCAN,
                up_to_rev=after.rev,
                asked_after_seq=before.seq,
            )
            if lines:
                views[channel_id] = await lines_view(
                    uow, channel, lines, since_seq=before.seq
                )
    return views


async def _memberships(
    uow: SqlAlchemyUnitOfWork, users: set[UUID], channel_ids: set[UUID]
) -> dict[UUID, set[UUID]]:
    """Which of ``channel_ids`` each reader is in: one read for them all."""
    if not users or not channel_ids:
        return {}
    rows = await uow.session.execute(
        select(ChannelMemberModel.user_id, ChannelMemberModel.channel_id).where(
            ChannelMemberModel.user_id.in_(sorted(users)),
            ChannelMemberModel.channel_id.in_(sorted(channel_ids)),
        )
    )
    joined: dict[UUID, set[UUID]] = {}
    for user_id, channel_id in rows:
        joined.setdefault(user_id, set()).add(channel_id)
    return joined


async def _seen_by(uow: SqlAlchemyUnitOfWork, caller: Caller) -> set[UUID]:
    """The pod's channels the reader may see now -- the most recently active
    ``SEEN_AT_JOIN`` of them; any other is added once it is sent to them."""
    rows = await uow.session.scalars(
        select(ChannelModel.id)
        .where(visible_to(caller.viewer))
        .order_by(ChannelModel.last_line_at.desc())
        .limit(SEEN_AT_JOIN)
    )
    return set(rows)


__all__ = ["InboxFeed"]
