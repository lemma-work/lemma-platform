"""Channel seats kept in memory, the way the channels tables keep them.

A surface asks channels where things sit: the source a place is (made, with
its channel, the first time), what was heard there, the conversation a person
has in a channel, and where a conversation's answer goes. A double that
answered each with a fresh object would certify code that can never find
anything it made; this one keys what it keeps as the unique indexes do:

* an **account** by its pod, platform, workspace and bot
  (``uq_external_accounts_key``), made the first time any of its places is
  heard, reached through whichever surface reported it last -- so a
  reinstalled bot takes over the places it had -- and given its bot in place
  when the surface that reached it with none learns it;
* a **source** by its account and the place on it
  (``account_id, coalesce(external_channel_id), coalesce(external_user_id)``),
  never by the surface: a source's ``surface_id`` is its account's, as the
  join reports it now;
* a **channel** by whose it is.

So the second message from the same place lands on the same source, a
reinstalled bot finds it, and two bots in different accounts never share one.

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
    AccountKey,
    ContactsHome,
    GroupHome,
    Home,
    MembersHome,
    Place,
    Source,
    SourceKind,
)
from app.modules.channels.contracts.places import ReplyAddress

#: A source's unique key: its account, and the place on it.
type _PlaceKey = tuple[UUID, str, str]
#: An account's unique key: its pod, platform, workspace and bot.
type _AccountKey = tuple[UUID, str, str, str | None]
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
        self.channels: dict[tuple[object, ...], UUID] = {}
        #: Each account a place was heard in, by its pod, platform and key.
        self.accounts: dict[_AccountKey, UUID] = {}
        #: The surface that reaches each account now (``None``: none does).
        self.account_surfaces: dict[UUID, UUID | None] = {}
        #: Each conversation's channel, as seating left it.
        self.placed: dict[UUID, UUID] = {}
        #: The persons ``lock_person`` was asked to serialise, in order.
        self.locked: list[UUID] = []
        self._places: dict[_PlaceKey, Source] = {}
        self._seated: dict[_SeatKey, UUID] = {}
        self._answers: dict[UUID, ReplyAddress] = {}
        self._lines: list[_Line] = []

    @property
    def sources(self) -> dict[_PlaceKey, Source]:
        """Every source, as a read through its account reports it now."""
        return {key: self._now(source) for key, source in self._places.items()}

    # -- what a test says ------------------------------------------------------

    def add(self, source: Source, *, account: AccountKey | None = None) -> Source:
        """A place the pod already knows, in the account ``source.account_id``,
        reached through ``source.surface_id``.

        With ``account`` (``account_of(surface)``) the account is the
        pod's account of that name, so ``source_for`` finds the place again;
        without, the account is reachable only by its id -- which is all a
        lookup by surface needs.
        """
        account_id = source.account_id
        if account is not None:
            account_id = self.accounts.setdefault(
                (source.pod_id, source.platform, account.workspace, account.bot),
                source.account_id,
            )
        self.account_surfaces[account_id] = source.surface_id
        return self._put(source.model_copy(update={"account_id": account_id}))

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
        account_id = self._account_for(pod_id, place)
        key = _place_key(account_id, place.external_channel_id, place.external_user_id)
        found = self._places.get(key)
        if found is not None:
            return self._now(found)
        now = datetime.now(timezone.utc)
        source = Source(
            id=uuid4(),
            channel_id=self._channel(pod_id, home, key),
            pod_id=pod_id,
            account_id=account_id,
            surface_id=self.account_surfaces.get(account_id),
            platform=place.platform,
            kind=place.kind,
            is_private=place.is_private,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
            title=place.title,
            created_at=now,
            updated_at=now,
        )
        return self._now(self._put(source))

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
            if source.kind is SourceKind.PERSON
            and source.platform == platform
            and source.external_user_id == external_user_id
            and (pod_id is None or source.pod_id == pod_id)
            and (surface_ids is None or self._reached_by(source, surface_ids))
        ]
        return sorted(found, key=_freshness, reverse=True)[: max(1, limit)]

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
        platform: str,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None,
    ) -> list[Source]:
        # As ``SourceRepository.hearing`` scopes it: these surfaces' accounts,
        # or -- no surfaces named -- every account on the platform.
        if surface_ids is not None and not surface_ids:
            return []
        found = [
            source
            for source in self.sources.values()
            if source.external_user_id is None
            and source.external_channel_id == external_channel_id
            and (
                self._reached_by(source, surface_ids)
                if surface_ids is not None
                else source.platform == platform
            )
        ]
        return sorted(found, key=_created)

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

    def _account_for(self, pod_id: UUID, place: Place) -> UUID:
        """As ``AccountRepository.account_for``: made the first time, with the
        surface that reported it; taken over by whichever surface reports it
        next; given its bot by the surface that reached it with none."""
        key = (pod_id, place.platform, place.account.workspace, place.account.bot)
        account_id = self.accounts.get(key)
        if account_id is None and place.account.bot is not None:
            botless = (pod_id, place.platform, place.account.workspace, None)
            found = self.accounts.get(botless)
            if (
                found is not None
                and self.account_surfaces.get(found) == place.surface_id
            ):
                account_id = self.accounts[key] = self.accounts.pop(botless)
        if account_id is None:
            account_id = self.accounts[key] = uuid4()
            self.account_surfaces[account_id] = place.surface_id
        elif place.surface_id is not None:
            self.account_surfaces[account_id] = place.surface_id
        return account_id

    def _reached_by(self, source: Source, surface_ids: Collection[UUID]) -> bool:
        return self.account_surfaces.get(source.account_id) in set(surface_ids)

    def _now(self, source: Source) -> Source:
        """The source as a read reports it: through its account's surface now."""
        surface_id = self.account_surfaces.get(source.account_id, source.surface_id)
        if surface_id == source.surface_id:
            return source
        return source.model_copy(update={"surface_id": surface_id})

    def _put(self, source: Source) -> Source:
        key = _place_key(
            source.account_id, source.external_channel_id, source.external_user_id
        )
        self._places[key] = source
        return self._now(source)

    def _by_id(self, source_id: UUID) -> Source | None:
        return next(
            (
                self._now(source)
                for source in self._places.values()
                if source.id == source_id
            ),
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
        self._put(source.model_copy(update=update))

    def _channel(self, pod_id: UUID, home: Home, place: _PlaceKey) -> UUID:
        match home:
            case MembersHome(user_id=user_id):
                key: tuple[object, ...] = ("DIRECT", pod_id, user_id)
            case ContactsHome(contact_id=contact_id):
                key = ("CONTACT", pod_id, contact_id)
            case GroupHome():
                key = ("GROUP", *place)
        return self.channels.setdefault(key, uuid4())


def _place_key(
    account_id: UUID, external_channel_id: str | None, external_user_id: str | None
) -> _PlaceKey:
    return (account_id, external_channel_id or "", external_user_id or "")


_LONG_AGO = datetime.now(timezone.utc) - timedelta(days=36500)


def _freshness(source: Source) -> datetime:
    return source.last_inbound_at or source.updated_at or _LONG_AGO


def _created(source: Source) -> datetime:
    return source.created_at or _LONG_AGO
