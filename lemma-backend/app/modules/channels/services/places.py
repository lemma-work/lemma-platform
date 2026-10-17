"""Which channel an outside place belongs to, decided the first time it is heard.

A source joins a channel once and stays: a private chat with a member is a
source of that member's DIRECT channel, a contact's is a source of theirs, a
group is a source of a GROUP channel of its own. After that the source's row
answers, whoever writes there next.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import Channel, Source, SourceKind
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository


class _LostTheRace(Exception):
    """Another writer added the group's source first; this channel goes back."""


@dataclass(frozen=True, slots=True)
class MembersHome:
    """A member's private chat: their DIRECT channel."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class ContactsHome:
    """A contact's private chat, web chat or email: their CONTACT channel."""

    contact_id: UUID


@dataclass(frozen=True, slots=True)
class GroupHome:
    """A chat several people read: a GROUP channel of its own."""

    title: str | None


type Home = MembersHome | ContactsHome | GroupHome


@dataclass(frozen=True, slots=True)
class Place:
    """An outside place, as the surface that hears it names it.

    ``surface_id`` is ``None`` for a web chat, which no surface carries: it is
    keyed by its widget and the visitor's session instead.
    """

    surface_id: UUID | None
    platform: str
    kind: SourceKind
    external_channel_id: str | None
    external_user_id: str | None
    title: str | None = None


class ChannelPlaces:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.channels = ChannelRepository(uow.session)
        self.session = uow.session

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source:
        """The source for ``place``, made -- with its channel -- the first time."""
        found = await self._find(pod_id, place)
        if found is not None:
            return found
        if isinstance(home, GroupHome):
            return await self._group_source(pod_id, place, home)
        channel = await self._home(pod_id, home)
        added = await self.channels.add_source(
            channel=channel,
            surface_id=place.surface_id,
            platform=place.platform,
            kind=place.kind,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
            title=place.title,
        )
        return added or await self._winner(pod_id, place)

    async def _group_source(
        self, pod_id: UUID, place: Place, home: GroupHome
    ) -> Source:
        """A group's channel and its source, made together or not at all.

        Two first messages in one group race to create both; the loser's
        channel is rolled back with its source, rather than left behind empty.
        """
        try:
            async with self.session.begin_nested():
                channel = await self.channels.open_group(
                    pod_id=pod_id, title=home.title or place.title
                )
                added = await self.channels.add_source(
                    channel=channel,
                    surface_id=place.surface_id,
                    platform=place.platform,
                    kind=place.kind,
                    external_channel_id=place.external_channel_id,
                    external_user_id=place.external_user_id,
                    title=place.title,
                )
                if added is None:
                    raise _LostTheRace
                return added
        except IntegrityError, _LostTheRace:
            return await self._winner(pod_id, place)

    async def open_pending_group(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID,
        platform: str,
        title: str,
        owner_user_id: UUID,
        request_id: str,
        answers_outsiders: bool,
    ) -> Source:
        """A group the bot asked a platform to create: its channel, and a place
        with no id until the platform confirms it."""
        channel = await self.channels.open_group(pod_id=pod_id, title=title)
        return await SourceRepository(self.session).add_pending(
            channel=channel,
            surface_id=surface_id,
            platform=platform,
            title=title,
            owner_user_id=owner_user_id,
            request_id=request_id,
            answers_outsiders=answers_outsiders,
        )

    async def _home(self, pod_id: UUID, home: MembersHome | ContactsHome) -> Channel:
        if isinstance(home, MembersHome):
            return await self.channels.direct(pod_id=pod_id, user_id=home.user_id)
        return await self.channels.for_contact(
            pod_id=pod_id, contact_id=home.contact_id
        )

    async def _find(self, pod_id: UUID, place: Place) -> Source | None:
        return await self.channels.find_source(
            pod_id=pod_id,
            surface_id=place.surface_id,
            platform=place.platform,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
        )

    async def _winner(self, pod_id: UUID, place: Place) -> Source:
        winner = await self._find(pod_id, place)
        if winner is None:  # pragma: no cover - the conflict named it
            raise LookupError("A source another writer added vanished")
        return winner
