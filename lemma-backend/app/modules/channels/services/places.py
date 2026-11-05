"""Which channel an outside place belongs to, decided the first time it is heard.

A place is in an external account; the account is made the first time any of
its places is heard, and keeps them across a reinstalled bot. A private chat
with a member is a source of that member's DIRECT channel, a contact's of
theirs, a group of a GROUP channel of its own -- private when the platform
keeps it to its members, public otherwise.

What a channel's sources say about its visibility is decided in one place,
``recompute_visibility``: a group with any private source is private. A group
made in Lemma keeps the visibility it was given until a private place joins
it, and nothing here makes a channel public again -- only a person may, and
only while none of its places is private (the channel PATCH).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    AccountKey,
    Channel,
    ChannelKind,
    Principal,
    SeatedBy,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.channels.infrastructure.accounts import AccountRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.channels.services.viewers import member_sees


class _LostTheRace(Exception):
    """Another writer added the group's source first; this channel goes back."""


@dataclass(frozen=True, slots=True)
class MembersHome:
    """A member's private chat: their DIRECT channel."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class ContactsHome:
    """A contact's private chat, web chat or email: their CONTACT channel.
    ``keeper_user_id``: the member who looks after the bot's or widget's
    contacts, who follows the channel from the place's first word -- what the
    contact says is new to them without their joining."""

    contact_id: UUID
    keeper_user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class GroupHome:
    """A chat several people read: a GROUP channel of its own. ``owner``: the
    member who brought the bot in, who is in it from the start."""

    title: str | None
    owner_user_id: UUID | None = None


type Home = MembersHome | ContactsHome | GroupHome


@dataclass(frozen=True, slots=True)
class Place:
    """An outside place, as the surface that hears it names it.

    ``account`` is the account's own name on its platform -- a Slack team and
    bot, a Teams tenant and bot, a WhatsApp number, a Telegram bot, an inbox,
    a web widget -- and ``surface_id`` the installation that reaches it now
    (``None`` for a web widget, which no surface carries).
    """

    surface_id: UUID | None
    platform: str
    account: AccountKey
    kind: SourceKind
    external_channel_id: str | None
    external_user_id: str | None
    title: str | None = None
    #: The platform keeps it to its members.
    is_private: bool = True


class ChannelPlaces:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session
        self.channels = ChannelRepository(uow.session)
        self.sources = SourceRepository(uow.session)
        self.accounts = AccountRepository(uow.session)
        self.members = MemberRepository(uow.session)

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source:
        """The source for ``place``, made -- with its channel -- the first time."""
        account = await self.accounts.account_for(
            pod_id=pod_id,
            platform=place.platform,
            key=place.account,
            surface_id=place.surface_id,
        )
        found = await self._find(account.id, place)
        if found is not None:
            return found
        if isinstance(home, GroupHome):
            return await self._group_source(pod_id, account.id, place, home)
        channel = await self._home(pod_id, home)
        added = await self.attach(channel, account.id, place)
        if added is None:
            return await self._winner(account.id, place)
        if isinstance(home, ContactsHome) and home.keeper_user_id is not None:
            await self._keeper_follows(channel, home.keeper_user_id)
        return added

    async def _group_source(
        self, pod_id: UUID, account_id: UUID, place: Place, home: GroupHome
    ) -> Source:
        """A group's channel and its source, made together or not at all."""
        try:
            async with self.session.begin_nested():
                # Public until its place says otherwise (``attach``).
                channel = await self.channels.open_group(
                    pod_id=pod_id,
                    title=home.title or place.title,
                    visibility=Visibility.PUBLIC,
                    created_by_user_id=home.owner_user_id,
                )
                added = await self.attach(channel, account_id, place)
                if added is None:
                    raise _LostTheRace
                return added
        except IntegrityError, _LostTheRace:
            return await self._winner(account_id, place)

    async def open_pending_group(
        self,
        *,
        pod_id: UUID,
        place: Place,
        title: str,
        owner_user_id: UUID,
        request_id: str,
        answers_outsiders: bool,
    ) -> Source:
        """A group the bot asked a platform to create: its private channel,
        with its owner in it, and a place with no id until confirmed."""
        account = await self.accounts.account_for(
            pod_id=pod_id,
            platform=place.platform,
            key=place.account,
            surface_id=place.surface_id,
        )
        channel = await self.channels.open_group(
            pod_id=pod_id,
            title=title,
            visibility=Visibility.PUBLIC,
            created_by_user_id=owner_user_id,
        )
        pending = await self.sources.add_pending(
            channel=channel,
            account_id=account.id,
            platform=place.platform,
            title=title,
            owner_user_id=owner_user_id,
            request_id=request_id,
            answers_outsiders=answers_outsiders,
        )
        await recompute_visibility(self.session, channel.id)
        return pending

    async def _home(self, pod_id: UUID, home: MembersHome | ContactsHome) -> Channel:
        if isinstance(home, MembersHome):
            return await self.channels.direct(pod_id=pod_id, user_id=home.user_id)
        return await self.channels.for_contact(
            pod_id=pod_id, contact_id=home.contact_id
        )

    async def _keeper_follows(self, channel: Channel, keeper_user_id: UUID) -> None:
        """The member looking after the place's contacts is in the contact's
        channel from its first word, when they may open it: its lines are
        counted as new for them. Only a new place seats them, so a keeper who
        left the channel is not put back by the contact's next line."""
        if await member_sees(self.uow, channel, keeper_user_id):
            await self.members.add_person(
                channel, Principal.member(keeper_user_id), seated_by=SeatedBy.ADDED
            )

    async def attach(
        self, channel: Channel, account_id: UUID, place: Place
    ) -> Source | None:
        """Add a place to a channel, which is private from now on if the place
        is; ``None`` when another writer added the place first."""
        added = await self.sources.add(
            channel=channel,
            account_id=account_id,
            platform=place.platform,
            kind=place.kind,
            is_private=place.is_private,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
            title=place.title,
        )
        if added is not None:
            await recompute_visibility(self.session, channel.id)
        return added

    async def _find(self, account_id: UUID, place: Place) -> Source | None:
        return await self.sources.find(
            account_id=account_id,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
        )

    async def _winner(self, account_id: UUID, place: Place) -> Source:
        winner = await self._find(account_id, place)
        if winner is None:  # pragma: no cover - the conflict named it
            raise LookupError("A source another writer added vanished")
        return winner

    async def join(self, channel: Channel, principal: Principal) -> None:
        """Somebody the platform shows is in a place is in its channel."""
        await self.members.add_person(channel, principal, seated_by=SeatedBy.PLATFORM)


async def recompute_visibility(session: AsyncSession, channel_id: UUID) -> bool:
    """The only writer of a channel's visibility from its sources: a group
    any of whose places is private is private. True if it changed.

    One way only. A place turning public again, or leaving, changes nothing:
    the people of a private place were told it was theirs, and only somebody
    in the pod may open it to everyone.
    """
    channels = ChannelRepository(session)
    channel = await channels.get(channel_id)
    if (
        channel is None
        or channel.kind is not ChannelKind.GROUP
        or channel.visibility is Visibility.PRIVATE
        or not await SourceRepository(session).any_private(channel_id)
    ):
        return False
    await channels.change(channel_id, {"visibility": Visibility.PRIVATE.value})
    return True
