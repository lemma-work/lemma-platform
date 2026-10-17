"""Channel seats kept in memory, the way the channels tables keep them.

A surface asks channels where things sit: the source a place is (made, with
its channel, the first time), what was heard there, the conversation a person
has in a channel, and where a conversation's answer goes. A double that
answered each with a fresh object would certify code that can never find
anything it made; this one keys a source by its pod and place and a channel by
whose it is, as the unique indexes do, so the second message from the same
place lands on the same source.

Lines are not posted here -- ``RecordingChannelLines`` records those. What a
conversation answers, and so where its reply goes, is said by a test with
``answer``: the line a person said at a place, taken up by a conversation.
A conversation is found in a channel only where a test seated one (``seat``),
or ``answer`` did.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.core.authorization.delegation import effective_agent_id
from app.modules.channels.contracts import (
    ContactsHome,
    GroupHome,
    Home,
    MembersHome,
    Place,
    Source,
    SourceKind,
)
from app.modules.channels.contracts.places import ReplyAddress

type _PlaceKey = tuple[UUID, UUID | None, str, str, str]
type _SeatKey = tuple[UUID, UUID, UUID]


@dataclass(frozen=True, slots=True)
class _Line:
    """Something a person said at a place, that a conversation took up."""

    source_id: UUID
    conversation_id: UUID
    #: Every id the line's event names its sender by.
    sender_ids: frozenset[str]
    external_thread_id: str | None


class InMemoryChannelSeats:
    def __init__(self) -> None:
        self.sources: dict[_PlaceKey, Source] = {}
        self.channels: dict[tuple[object, ...], UUID] = {}
        #: Each conversation's channel, as seating left it.
        self.placed: dict[UUID, UUID] = {}
        #: The persons ``lock_person`` was asked to serialise, in order.
        self.locked: list[UUID] = []
        self._seated: dict[_SeatKey, UUID] = {}
        self._answers: dict[UUID, ReplyAddress] = {}
        self._lines: list[_Line] = []

    # -- what a test says ------------------------------------------------------

    def add(self, source: Source) -> Source:
        """A place the pod already knows."""
        self.sources[_key_of(source)] = source
        return source

    def answer(
        self,
        conversation_id: UUID,
        source: Source,
        ref: Mapping[str, object] | None,
        *,
        person: UUID | None = None,
        agent_id: UUID | None = None,
    ) -> None:
        """``conversation_id`` took up ``ref``, a line said at ``source``: its
        reply goes there. With ``person``, it is that person's conversation
        with ``agent_id`` in the place's channel too."""
        source = self.add(
            source.model_copy(update={"delivery_ref": dict(ref) if ref else None})
        )
        self._answers[conversation_id] = ReplyAddress(
            source=source, ref=dict(ref) if ref else None
        )
        named = ref or {}
        senders = (
            named.get("sender_external_user_id"),
            named.get("sender_aad_object_id"),
        )
        thread = named.get("external_thread_id")
        self._lines.append(
            _Line(
                source_id=source.id,
                conversation_id=conversation_id,
                sender_ids=frozenset(s for s in senders if isinstance(s, str)),
                external_thread_id=thread if isinstance(thread, str) else None,
            )
        )
        if person is not None:
            self.seat(
                conversation_id,
                channel_id=source.channel_id,
                pod_id=source.pod_id,
                agent_id=agent_id,
                person=person,
            )

    def seat(
        self,
        conversation_id: UUID,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        person: UUID,
    ) -> None:
        """Give ``person`` -- a member's or a contact's id -- this conversation."""
        agent = effective_agent_id(agent_id, pod_id=pod_id)
        self._seated[(channel_id, agent, person)] = conversation_id
        self.placed[conversation_id] = channel_id

    # -- ChannelSeats ----------------------------------------------------------

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source:
        key = (
            pod_id,
            place.surface_id,
            place.platform,
            place.external_channel_id or "",
            place.external_user_id or "",
        )
        found = self.sources.get(key)
        if found is not None:
            return found
        now = datetime.now(timezone.utc)
        source = Source(
            id=uuid4(),
            channel_id=self._channel(pod_id, home, key),
            pod_id=pod_id,
            surface_id=place.surface_id,
            platform=place.platform,
            kind=place.kind,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
            title=place.title,
            created_at=now,
            updated_at=now,
        )
        self.sources[key] = source
        return source

    async def heard_at(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None:
        self._touch(source_id, delivery_ref, heard=True)

    async def addressed_at(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None:
        self._touch(source_id, delivery_ref, heard=False)

    async def lock_person(
        self, *, channel_id: UUID, pod_id: UUID, agent_id: UUID | None, person_id: UUID
    ) -> None:
        self.locked.append(person_id)

    async def conversation_in_channel(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        user_id: UUID,
        contact_id: UUID | None,
    ) -> UUID | None:
        agent = effective_agent_id(agent_id, pod_id=pod_id)
        return self._seated.get((channel_id, agent, contact_id or user_id))

    async def direct_channel(self, *, pod_id: UUID, user_id: UUID) -> UUID:
        return self.channels.setdefault(("DIRECT", pod_id, user_id), uuid4())

    async def reply_address(self, conversation_id: UUID) -> ReplyAddress | None:
        answer = self._answers.get(conversation_id)
        if answer is None:
            return None
        # The place as it is now, not as it was when the line was said.
        current = self._by_id(answer.source.id) or answer.source
        return ReplyAddress(source=current, ref=answer.ref)

    async def person_places(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID] | None = None,
        pod_id: UUID | None = None,
        limit: int = 10,
    ) -> list[Source]:
        found = [
            source
            for source in self.sources.values()
            if source.kind is SourceKind.PRIVATE
            and source.platform == platform
            and source.external_user_id == external_user_id
            and (surface_ids is None or source.surface_id in set(surface_ids))
            and (pod_id is None or source.pod_id == pod_id)
        ]
        return sorted(found, key=_freshness, reverse=True)[:limit]

    async def people_places(
        self,
        *,
        platform: str,
        external_user_ids: Collection[str],
        surface_id: UUID,
        pod_id: UUID,
    ) -> dict[str, Source]:
        freshest: dict[str, Source] = {}
        for person in external_user_ids:
            found = await self.person_places(
                platform=platform,
                external_user_id=person,
                surface_ids=[surface_id],
                pod_id=pod_id,
                limit=1,
            )
            if found:
                freshest[person] = found[0]
        return freshest

    async def places_hearing(
        self,
        *,
        platform: str | None,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None = None,
    ) -> list[Source]:
        return [
            source
            for source in self.sources.values()
            if source.external_user_id is None
            and source.external_channel_id == external_channel_id
            and (platform is None or source.platform == platform)
            and (surface_ids is None or source.surface_id in set(surface_ids))
        ]

    async def conversation_at(
        self,
        source_id: UUID,
        *,
        sender_external_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> UUID | None:
        for line in reversed(self._lines):
            if (
                line.source_id == source_id
                and (
                    sender_external_id is None or sender_external_id in line.sender_ids
                )
                and (
                    external_thread_id is None
                    or line.external_thread_id == external_thread_id
                )
            ):
                return line.conversation_id
        return None

    # -- keeping ---------------------------------------------------------------

    def _by_id(self, source_id: UUID) -> Source | None:
        return next(
            (source for source in self.sources.values() if source.id == source_id),
            None,
        )

    def _touch(
        self, source_id: UUID, delivery_ref: Mapping[str, object], *, heard: bool
    ) -> None:
        source = self._by_id(source_id)
        if source is None:
            return
        now = datetime.now(timezone.utc)
        update: dict[str, object] = {
            "delivery_ref": dict(delivery_ref),
            "updated_at": now,
        }
        if heard:
            update["last_inbound_at"] = now
        self.add(source.model_copy(update=update))

    def _channel(self, pod_id: UUID, home: Home, place: _PlaceKey) -> UUID:
        match home:
            case MembersHome(user_id=user_id):
                key: tuple[object, ...] = ("DIRECT", pod_id, user_id)
            case ContactsHome(contact_id=contact_id):
                key = ("CONTACT", pod_id, contact_id)
            case GroupHome():
                key = ("GROUP", *place)
        return self.channels.setdefault(key, uuid4())


def _key_of(source: Source) -> _PlaceKey:
    return (
        source.pod_id,
        source.surface_id,
        source.platform,
        source.external_channel_id or "",
        source.external_user_id or "",
    )


def _freshness(source: Source) -> datetime:
    when = source.last_inbound_at or source.updated_at
    return when or datetime.now(timezone.utc) - timedelta(days=36500)
