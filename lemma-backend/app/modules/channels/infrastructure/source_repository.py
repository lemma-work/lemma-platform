"""Channel sources as places: found by who or what they are, and how to reach them.

A source is a place in an external account -- a person's private chat, a
group, a shared channel, an email thread, a web chat -- and the address its
account writes back to. A surface asks by the surfaces it serves; every
lookup is scoped to those surfaces' accounts or to one pod, so one pod's bot
never finds, changes or forgets another pod's place. Every read is bounded.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Channel, SenderKind, Source, SourceKind
from app.modules.channels.infrastructure.accounts import accounts_of_surfaces
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelSourceModel,
    ExternalAccountModel,
)
from app.modules.channels.infrastructure.rows import now, source_entity

#: The most places one lookup returns.
MAX_PLACES = 50

#: The settings of a shared place a caller may change.
GROUP_SETTINGS = frozenset(
    {"owner_user_id", "answers_outsiders", "shared_externally", "invite_link", "title"}
)


def _with_surface() -> Select[tuple[ChannelSourceModel, UUID | None]]:
    return select(ChannelSourceModel, ExternalAccountModel.surface_id).join(
        ExternalAccountModel, ExternalAccountModel.id == ChannelSourceModel.account_id
    )


def _scope(
    surface_ids: Collection[UUID] | None, platform: str | None
) -> ColumnElement[bool]:
    """Places on these surfaces' accounts, or -- for ids a platform makes
    unique across every account (Meta's group and request ids) -- on that
    platform. One of the two is required: no lookup reaches every pod."""
    if surface_ids is not None:
        return ChannelSourceModel.account_id.in_(accounts_of_surfaces(surface_ids))
    if platform is not None:
        return ChannelSourceModel.platform == platform
    raise ValueError("A place is looked up on some surfaces, or one platform")


def place_lookup(
    *,
    account_id: UUID,
    external_channel_id: str | None,
    external_user_id: str | None,
) -> Select[tuple[ChannelSourceModel, UUID | None]]:
    """One place of an account, by ``uq_channel_sources_place``: what every
    inbound message reads first."""
    source = ChannelSourceModel
    return _with_surface().where(
        source.account_id == account_id,
        func.coalesce(source.external_channel_id, "") == (external_channel_id or ""),
        func.coalesce(source.external_user_id, "") == (external_user_id or ""),
        # The index's own predicate: the planner uses a partial index only for
        # a query that implies it, and the coalesces above do not.
        or_(
            source.external_channel_id.is_not(None),
            source.external_user_id.is_not(None),
        ),
    )


def _sources(
    rows: Iterable[Row[tuple[ChannelSourceModel, UUID | None]]],
) -> list[Source]:
    return [source_entity(row[0], surface_id=row[1]) for row in rows]


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, source_id: UUID) -> Source | None:
        found = (
            await self.session.execute(
                _with_surface().where(ChannelSourceModel.id == source_id)
            )
        ).first()
        return source_entity(found[0], surface_id=found[1]) if found else None

    async def find(
        self,
        *,
        account_id: UUID,
        external_channel_id: str | None,
        external_user_id: str | None,
    ) -> Source | None:
        found = (
            await self.session.execute(
                place_lookup(
                    account_id=account_id,
                    external_channel_id=external_channel_id,
                    external_user_id=external_user_id,
                )
            )
        ).first()
        return source_entity(found[0], surface_id=found[1]) if found else None

    async def add(
        self,
        *,
        channel: Channel,
        account_id: UUID,
        platform: str,
        kind: SourceKind,
        is_private: bool,
        external_channel_id: str | None,
        external_user_id: str | None,
        title: str | None,
    ) -> Source | None:
        """Add a place to a channel; ``None`` when another writer added it first."""
        result = await self.session.execute(
            insert(ChannelSourceModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                account_id=account_id,
                platform=platform,
                kind=kind.value,
                is_private=is_private,
                external_channel_id=external_channel_id,
                external_user_id=external_user_id,
                title=title,
            )
            .on_conflict_do_nothing()
            .returning(ChannelSourceModel.id)
        )
        added = result.scalar()
        return await self.get(added) if added is not None else None

    async def of_channel(self, channel_id: UUID) -> list[Source]:
        """A channel's places, the one heard from most recently first."""
        source = ChannelSourceModel
        rows = await self.session.execute(
            _with_surface()
            .where(source.channel_id == channel_id)
            .order_by(
                func.coalesce(source.last_inbound_at, source.updated_at).desc(),
                source.id,
            )
            .limit(MAX_PLACES)
        )
        return _sources(rows)

    async def any_private(self, channel_id: UUID) -> bool:
        """Whether any of a channel's places is kept to its members."""
        source = ChannelSourceModel
        private = select(source.id).where(
            source.channel_id == channel_id, source.is_private.is_(True)
        )
        return await self.session.scalar(private.limit(1)) is not None

    async def in_channel(
        self, channel_id: UUID, source_ids: Collection[UUID]
    ) -> set[UUID]:
        """Which of ``source_ids`` are places of this channel."""
        if not source_ids:
            return set()
        rows = await self.session.scalars(
            select(ChannelSourceModel.id).where(
                ChannelSourceModel.channel_id == channel_id,
                ChannelSourceModel.id.in_(sorted(source_ids)),
            )
        )
        return set(rows)

    async def of_channels(
        self, channel_ids: Collection[UUID]
    ) -> dict[UUID, list[Source]]:
        if not channel_ids:
            return {}
        rows = await self.session.execute(
            _with_surface()
            .where(ChannelSourceModel.channel_id.in_(sorted(channel_ids)))
            .order_by(ChannelSourceModel.created_at)
            .limit(MAX_PLACES * 20)
        )
        grouped: dict[UUID, list[Source]] = {}
        for source in _sources(rows):
            grouped.setdefault(source.channel_id, []).append(source)
        return grouped

    # -- finding places, for the surfaces that hear them ------------------------

    async def hearing(
        self,
        *,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None = None,
        platform: str | None = None,
    ) -> list[Source]:
        """The shared places on this chat, among these surfaces' accounts --
        or, for a request whose signature proved only its platform, on every
        account there."""
        if surface_ids is not None and not surface_ids:
            return []
        source = ChannelSourceModel
        rows = await self.session.execute(
            _with_surface()
            .where(
                source.external_channel_id == external_channel_id,
                source.external_user_id.is_(None),
                _scope(surface_ids, platform),
            )
            .order_by(source.created_at)
            .limit(MAX_PLACES)
        )
        return _sources(rows)

    async def person(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID] | None = None,
        pod_id: UUID | None = None,
        limit: int = MAX_PLACES,
    ) -> list[Source]:
        """One person's private places on a platform, freshest first, among
        these surfaces' accounts, in this pod, or -- neither named, for a
        request whose signature proved only its platform -- anywhere on it."""
        source = ChannelSourceModel
        statement = _with_surface().where(
            source.platform == platform,
            source.external_user_id == external_user_id,
            source.kind == SourceKind.PERSON.value,
        )
        if pod_id is not None:
            statement = statement.where(source.pod_id == pod_id)
        if surface_ids is not None:
            statement = statement.where(
                source.account_id.in_(accounts_of_surfaces(surface_ids))
            )
        rows = await self.session.execute(
            statement.order_by(
                func.coalesce(source.last_inbound_at, source.updated_at).desc()
            ).limit(max(1, min(limit, MAX_PLACES)))
        )
        return _sources(rows)

    async def people(
        self,
        *,
        platform: str,
        external_user_ids: Collection[str],
        surface_id: UUID,
        pod_id: UUID,
    ) -> dict[str, Source]:
        """Each of these people's freshest private place on one surface's
        account, in one pod."""
        if not external_user_ids:
            return {}
        source = ChannelSourceModel
        rows = await self.session.execute(
            _with_surface()
            .where(
                source.platform == platform,
                source.pod_id == pod_id,
                source.kind == SourceKind.PERSON.value,
                source.external_user_id.in_(sorted(external_user_ids)),
                source.account_id.in_(accounts_of_surfaces([surface_id])),
            )
            .order_by(func.coalesce(source.last_inbound_at, source.updated_at).desc())
            .limit(MAX_PLACES * 4)
        )
        freshest: dict[str, Source] = {}
        for found in _sources(rows):
            if found.external_user_id is not None:
                freshest.setdefault(found.external_user_id, found)
        return freshest

    async def shared_of_pod(
        self, pod_id: UUID, *, limit: int, surface_id: UUID | None = None
    ) -> list[Source]:
        """A pod's shared places, most recently changed first; one surface's
        when named."""
        source = ChannelSourceModel
        statement = _with_surface().where(
            source.pod_id == pod_id, source.kind == SourceKind.CHAT.value
        )
        if surface_id is not None:
            statement = statement.where(ExternalAccountModel.surface_id == surface_id)
        rows = await self.session.execute(
            statement.order_by(source.updated_at.desc()).limit(
                max(1, min(limit, MAX_PLACES * 4))
            )
        )
        return _sources(rows)

    async def by_request(self, *, account_id: UUID, request_id: str) -> Source | None:
        found = (
            await self.session.execute(
                _with_surface().where(
                    ChannelSourceModel.account_id == account_id,
                    ChannelSourceModel.request_id == request_id,
                )
            )
        ).first()
        return source_entity(found[0], surface_id=found[1]) if found else None

    async def by_request_on_surfaces(
        self,
        *,
        request_id: str,
        surface_ids: Collection[UUID] | None = None,
        platform: str | None = None,
    ) -> Source | None:
        found = (
            await self.session.execute(
                _with_surface()
                .where(
                    ChannelSourceModel.request_id == request_id,
                    _scope(surface_ids, platform),
                )
                .limit(1)
            )
        ).first()
        return source_entity(found[0], surface_id=found[1]) if found else None

    async def owned(
        self, *, surface_id: UUID, owner_user_id: UUID, title: str
    ) -> Source | None:
        """A shared place this member answers for on this surface, by title."""
        source = ChannelSourceModel
        found = (
            await self.session.execute(
                _with_surface()
                .where(
                    ExternalAccountModel.surface_id == surface_id,
                    source.owner_user_id == owner_user_id,
                    source.title == title,
                    source.kind == SourceKind.CHAT.value,
                )
                .order_by(source.created_at.desc())
                .limit(1)
            )
        ).first()
        return source_entity(found[0], surface_id=found[1]) if found else None

    # -- what was heard ---------------------------------------------------------

    async def heard(self, source_id: UUID, delivery_ref: Mapping[str, object]) -> None:
        """The account heard this place just now; this is how to answer it."""
        at = now()
        await self.session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source_id)
            .values(delivery_ref=dict(delivery_ref), last_inbound_at=at, updated_at=at)
        )

    async def addressed(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None:
        """How to write to this place, learnt from writing to it first."""
        await self.session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source_id)
            .values(delivery_ref=dict(delivery_ref), updated_at=now())
        )

    async def reply_address(
        self, conversation_id: UUID
    ) -> tuple[Source, dict[str, object] | None] | None:
        """Where a conversation's answer goes, and what answers it in place.

        The place of the latest line a person said in it, with what its
        account kept to answer that line; else the latest line with a place
        at all -- heard there or carried there -- and that place's last word. None for a conversation said
        nowhere outside Lemma.
        """
        message = ChannelMessageModel
        heard = (
            await self.session.execute(
                _with_surface()
                .add_columns(message.reply_ref)
                .join(message, message.source_id == ChannelSourceModel.id)
                .where(
                    message.conversation_id == conversation_id,
                    message.sender_kind != SenderKind.AGENT.value,
                )
                .order_by(message.seq.desc())
                .limit(1)
            )
        ).first()
        if heard is not None:
            source = source_entity(heard[0], surface_id=heard[1])
            return source, heard[2] if heard[2] is not None else source.delivery_ref
        placed = (
            await self.session.execute(
                _with_surface()
                .join(
                    message,
                    func.coalesce(message.source_id, message.target_source_id)
                    == ChannelSourceModel.id,
                )
                .where(message.conversation_id == conversation_id)
                .order_by(message.seq.desc())
                .limit(1)
            )
        ).first()
        if placed is None:
            return None
        source = source_entity(placed[0], surface_id=placed[1])
        return source, source.delivery_ref

    async def conversation_at(
        self,
        source_id: UUID,
        *,
        sender_external_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> UUID | None:
        """The conversation that last took up a line said here -- by this
        sender, and in this thread, when they are named."""
        message = ChannelMessageModel
        statement = select(message.conversation_id).where(
            message.source_id == source_id,
            message.conversation_id.is_not(None),
            message.sender_kind != SenderKind.AGENT.value,
        )
        if sender_external_id is not None:
            statement = statement.where(
                or_(
                    message.reply_ref["sender_external_user_id"].astext
                    == sender_external_id,
                    message.reply_ref["sender_aad_object_id"].astext
                    == sender_external_id,
                )
            )
        if external_thread_id is not None:
            statement = statement.where(
                message.external_thread_id == external_thread_id
            )
        return await self.session.scalar(
            statement.order_by(message.seq.desc()).limit(1)
        )

    # -- a shared place's settings ----------------------------------------------

    async def add_pending(
        self,
        *,
        channel: Channel,
        account_id: UUID,
        platform: str,
        title: str,
        owner_user_id: UUID,
        request_id: str,
        answers_outsiders: bool,
    ) -> Source:
        """A group the bot asked the platform to create: no id until confirmed."""
        row = ChannelSourceModel(
            channel_id=channel.id,
            pod_id=channel.pod_id,
            account_id=account_id,
            platform=platform,
            kind=SourceKind.CHAT.value,
            is_private=True,
            title=title,
            owner_user_id=owner_user_id,
            request_id=request_id,
            answers_outsiders=answers_outsiders,
        )
        self.session.add(row)
        await self.session.flush()
        found = await self.get(row.id)
        assert found is not None
        return found

    async def confirm(
        self,
        *,
        request_id: str,
        external_channel_id: str,
        title: str | None,
        invite_link: str | None,
        surface_ids: Collection[UUID] | None = None,
        platform: str | None = None,
    ) -> Source | None:
        """The platform made the group: it has an id now. A replay changes nothing."""
        source = ChannelSourceModel
        values: dict[str, object] = {
            "external_channel_id": external_channel_id,
            "updated_at": now(),
        }
        if title:
            values["title"] = title
        if invite_link:
            values["invite_link"] = invite_link
        await self.session.execute(
            update(source)
            .where(
                source.request_id == request_id,
                source.external_channel_id.is_(None),
                _scope(surface_ids, platform),
            )
            .values(**values)
        )
        return await self.by_request_on_surfaces(
            request_id=request_id, surface_ids=surface_ids, platform=platform
        )

    async def set(self, source_id: UUID, values: Mapping[str, object]) -> None:
        """Change a shared place's settings; only the ones a caller may change."""
        unknown = set(values) - GROUP_SETTINGS
        if unknown:
            raise ValueError(f"Not a place setting: {sorted(unknown)}")
        if values:
            await self.session.execute(
                update(ChannelSourceModel)
                .where(ChannelSourceModel.id == source_id)
                .values(**values, updated_at=now())
            )

    async def forget_pending(
        self,
        *,
        request_id: str,
        surface_ids: Collection[UUID] | None = None,
        platform: str | None = None,
    ) -> None:
        """A creation the platform refused: there is no group to keep."""
        await self.session.execute(
            delete(ChannelSourceModel).where(
                ChannelSourceModel.request_id == request_id,
                ChannelSourceModel.external_channel_id.is_(None),
                _scope(surface_ids, platform),
            )
        )

    async def surfaces_hearing(
        self, *, platform: str, external_channel_id: str
    ) -> list[UUID]:
        """The surfaces whose bots are in this chat, for routing what arrives
        there. Read only: it changes nothing in any pod."""
        rows = await self.session.scalars(
            select(ExternalAccountModel.surface_id)
            .join(
                ChannelSourceModel,
                ChannelSourceModel.account_id == ExternalAccountModel.id,
            )
            .where(
                ChannelSourceModel.platform == platform,
                ChannelSourceModel.external_channel_id == external_channel_id,
                ChannelSourceModel.external_user_id.is_(None),
                ExternalAccountModel.surface_id.is_not(None),
            )
            .limit(MAX_PLACES)
        )
        return [row for row in rows if row is not None]

    async def forget_chat(
        self,
        *,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None = None,
        platform: str | None = None,
    ) -> None:
        """A group the platform deleted, on these surfaces' accounts: nobody can
        speak there. Its channel stays, with everything said in it."""
        await self.session.execute(
            delete(ChannelSourceModel).where(
                ChannelSourceModel.external_channel_id == external_channel_id,
                ChannelSourceModel.external_user_id.is_(None),
                _scope(surface_ids, platform),
            )
        )

    async def became_private(self, source_id: UUID) -> bool:
        """The platform now keeps this place to its members. True if it was
        open before -- never the reverse: only the platform makes one private,
        and nobody here makes one public behind its back."""
        result = await self.session.execute(
            update(ChannelSourceModel)
            .where(
                ChannelSourceModel.id == source_id,
                ChannelSourceModel.is_private.is_(False),
            )
            .values(is_private=True, updated_at=now())
            .returning(ChannelSourceModel.id)
        )
        return result.first() is not None
