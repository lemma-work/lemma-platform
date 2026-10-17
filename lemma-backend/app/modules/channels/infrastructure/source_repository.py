"""Channel sources as places: found by who or what they are, and how to reach them.

A source is where a surface hears something -- a person's private chat, a
group, a shared channel, an email thread -- and the address the surface writes
back to. These are the reads a surface makes of it, and the settings a shared
place keeps: who answers for its outsiders, whether they are answered, the
link people join a WhatsApp group by, and the creation the bot asked for.
Every read is bounded.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Channel, SenderKind, Source, SourceKind
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.repository import source_entity

#: The most places one lookup returns. A chat is heard by a handful of pods'
#: bots; a person writes from a handful of numbers.
MAX_PLACES = 50

#: The settings of a shared place a caller may change.
GROUP_SETTINGS = frozenset(
    {"owner_user_id", "answers_outsiders", "shared_externally", "invite_link", "title"}
)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- finding places ---------------------------------------------------------

    async def hearing(
        self,
        *,
        platform: str | None,
        external_channel_id: str,
        surface_ids: Collection[UUID] | None = None,
    ) -> list[Source]:
        """The shared places on this chat, one per surface that hears it.

        ``platform`` None for a caller that holds the surfaces and not their
        platform: a chat id is one platform's on one surface.
        """
        source = ChannelSourceModel
        statement = select(source).where(
            source.external_channel_id == external_channel_id,
            source.external_user_id.is_(None),
        )
        if platform is not None:
            statement = statement.where(source.platform == platform)
        if surface_ids is not None:
            statement = statement.where(source.surface_id.in_(sorted(surface_ids)))
        rows = await self.session.scalars(
            statement.order_by(source.created_at).limit(MAX_PLACES)
        )
        return [source_entity(row) for row in rows]

    async def person(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID] | None = None,
        pod_id: UUID | None = None,
        limit: int = MAX_PLACES,
    ) -> list[Source]:
        """One person's private places on a platform, freshest first."""
        source = ChannelSourceModel
        statement = select(source).where(
            source.platform == platform,
            source.external_user_id == external_user_id,
            source.kind == SourceKind.PRIVATE.value,
        )
        if pod_id is not None:
            statement = statement.where(source.pod_id == pod_id)
        if surface_ids is not None:
            statement = statement.where(source.surface_id.in_(sorted(surface_ids)))
        rows = await self.session.scalars(
            statement.order_by(
                func.coalesce(source.last_inbound_at, source.updated_at).desc()
            ).limit(max(1, min(limit, MAX_PLACES)))
        )
        return [source_entity(row) for row in rows]

    async def people(
        self,
        *,
        platform: str,
        external_user_ids: Collection[str],
        surface_id: UUID,
        pod_id: UUID,
    ) -> dict[str, Source]:
        """Each of these people's freshest private place on one surface, in
        one pod. Somebody who never wrote to it is absent."""
        if not external_user_ids:
            return {}
        source = ChannelSourceModel
        rows = await self.session.scalars(
            select(source)
            .where(
                source.platform == platform,
                source.surface_id == surface_id,
                source.pod_id == pod_id,
                source.kind == SourceKind.PRIVATE.value,
                source.external_user_id.in_(sorted(external_user_ids)),
            )
            .order_by(func.coalesce(source.last_inbound_at, source.updated_at).desc())
            .limit(MAX_PLACES * 4)
        )
        freshest: dict[str, Source] = {}
        for row in rows:
            if row.external_user_id is not None:
                freshest.setdefault(row.external_user_id, source_entity(row))
        return freshest

    async def of_channel(self, channel_id: UUID, *, kind: SourceKind) -> list[Source]:
        """A channel's places of one kind, freshest first."""
        source = ChannelSourceModel
        rows = await self.session.scalars(
            select(source)
            .where(source.channel_id == channel_id, source.kind == kind.value)
            .order_by(func.coalesce(source.last_inbound_at, source.updated_at).desc())
            .limit(MAX_PLACES)
        )
        return [source_entity(row) for row in rows]

    async def shared_of_pod(self, pod_id: UUID, *, limit: int) -> list[Source]:
        """A pod's shared places on its surfaces, most recently changed first."""
        source = ChannelSourceModel
        rows = await self.session.scalars(
            select(source)
            .where(
                source.pod_id == pod_id,
                source.kind == SourceKind.SHARED.value,
                source.surface_id.is_not(None),
            )
            .order_by(source.updated_at.desc())
            .limit(max(1, min(limit, MAX_PLACES * 4)))
        )
        return [source_entity(row) for row in rows]

    async def by_request(self, *, platform: str, request_id: str) -> Source | None:
        row = await self.session.scalar(
            select(ChannelSourceModel).where(
                ChannelSourceModel.platform == platform,
                ChannelSourceModel.request_id == request_id,
            )
        )
        return source_entity(row) if row else None

    async def owned(
        self, *, surface_id: UUID, owner_user_id: UUID, title: str
    ) -> Source | None:
        """A shared place this member answers for on this surface, by its title."""
        row = await self.session.scalar(
            select(ChannelSourceModel)
            .where(
                ChannelSourceModel.surface_id == surface_id,
                ChannelSourceModel.owner_user_id == owner_user_id,
                ChannelSourceModel.title == title,
                ChannelSourceModel.kind == SourceKind.SHARED.value,
            )
            .order_by(ChannelSourceModel.created_at.desc())
            .limit(1)
        )
        return source_entity(row) if row else None

    # -- what was heard ---------------------------------------------------------

    async def heard(self, source_id: UUID, delivery_ref: Mapping[str, object]) -> None:
        """The surface heard this place just now; this is how to answer it."""
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source_id)
            .values(
                delivery_ref=dict(delivery_ref), last_inbound_at=now, updated_at=now
            )
        )

    async def addressed(
        self, source_id: UUID, delivery_ref: Mapping[str, object]
    ) -> None:
        """How to write to this place, learnt from writing to it first: a cold
        email's thread. Nobody there has said anything yet."""
        await self.session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source_id)
            .values(
                delivery_ref=dict(delivery_ref), updated_at=datetime.now(timezone.utc)
            )
        )

    async def reply_address(
        self, conversation_id: UUID
    ) -> tuple[Source, dict[str, object] | None] | None:
        """Where a conversation's answer goes, and what answers it in place.

        The place of the latest line a person said in it, with what its
        surface kept to answer that line -- a web chat keeps nothing, and is
        answered on its page; else the latest line with a place at all -- a
        conversation the pod opened, a notification, a cold email -- and that
        place's last word. None for a conversation said nowhere outside Lemma.
        """
        message = ChannelMessageModel
        heard = await self.session.execute(
            select(ChannelSourceModel, message.reply_ref)
            .join(message, message.source_id == ChannelSourceModel.id)
            .where(
                message.conversation_id == conversation_id,
                message.sender_kind != SenderKind.AGENT.value,
            )
            .order_by(message.created_at.desc())
            .limit(1)
        )
        found = heard.first()
        if found is not None:
            source = source_entity(found[0])
            return source, found[1] if found[1] is not None else source.delivery_ref
        placed = await self.session.scalar(
            select(ChannelSourceModel)
            .join(message, message.source_id == ChannelSourceModel.id)
            .where(message.conversation_id == conversation_id)
            .order_by(message.created_at.desc())
            .limit(1)
        )
        if placed is None:
            return None
        source = source_entity(placed)
        return source, source.delivery_ref

    async def conversation_at(
        self,
        source_id: UUID,
        *,
        sender_external_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> UUID | None:
        """The conversation that last took up a line said here -- by this
        sender, and in this thread, when they are named.

        A sender is matched by either id the line's event names them by: the
        platform's own, or the directory id a platform also gives (Teams' AAD
        object id), which is the one a person's identity resolves to.
        """
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
            statement.order_by(message.created_at.desc()).limit(1)
        )

    # -- a shared place's settings ----------------------------------------------

    async def add_pending(
        self,
        *,
        channel: Channel,
        surface_id: UUID,
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
            surface_id=surface_id,
            platform=platform,
            kind=SourceKind.SHARED.value,
            title=title,
            owner_user_id=owner_user_id,
            request_id=request_id,
            answers_outsiders=answers_outsiders,
        )
        self.session.add(row)
        await self.session.flush()
        return source_entity(row)

    async def confirm(
        self,
        *,
        platform: str,
        request_id: str,
        external_channel_id: str,
        title: str | None,
        invite_link: str | None,
    ) -> Source | None:
        """The platform made the group: it has an id now. A replay changes nothing."""
        source = ChannelSourceModel
        values: dict[str, object] = {
            "external_channel_id": external_channel_id,
            "updated_at": datetime.now(timezone.utc),
        }
        if title:
            values["title"] = title
        if invite_link:
            values["invite_link"] = invite_link
        await self.session.execute(
            update(source)
            .where(
                source.platform == platform,
                source.request_id == request_id,
                source.external_channel_id.is_(None),
            )
            .values(**values)
        )
        return await self.by_request(platform=platform, request_id=request_id)

    async def set(self, source_id: UUID, values: Mapping[str, object]) -> None:
        """Change a shared place's settings; only the ones a caller may change."""
        unknown = set(values) - GROUP_SETTINGS
        if unknown:
            raise ValueError(f"Not a place setting: {sorted(unknown)}")
        if not values:
            return
        await self.session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source_id)
            .values(**values, updated_at=datetime.now(timezone.utc))
        )

    async def forget_pending(self, *, platform: str, request_id: str) -> None:
        """A creation the platform refused: there is no group to keep."""
        await self.session.execute(
            delete(ChannelSourceModel).where(
                ChannelSourceModel.platform == platform,
                ChannelSourceModel.request_id == request_id,
                ChannelSourceModel.external_channel_id.is_(None),
            )
        )

    async def forget_chat(self, *, platform: str, external_channel_id: str) -> None:
        """A group the platform deleted: nobody can speak there. Its channel
        stays, with everything said in it."""
        await self.session.execute(
            delete(ChannelSourceModel).where(
                ChannelSourceModel.platform == platform,
                ChannelSourceModel.external_channel_id == external_channel_id,
                ChannelSourceModel.external_user_id.is_(None),
            )
        )
