"""Reading and writing channels, their sources, people and timeline.

Every find-or-create here runs its insert in a savepoint behind a unique index,
the way contacts open: two first messages from one place arrive together often
enough, both read "nothing yet", and the loser re-reads the winner's row
instead of failing or making a second one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Participant,
    Principal,
    Sender,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.channels.infrastructure.models import (
    ChannelAgentModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelParticipantModel,
    ChannelSourceModel,
)

#: The most lines one read of a timeline returns.
MAX_LINES = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _channel(row: ChannelModel) -> Channel:
    return Channel(
        id=row.id,
        pod_id=row.pod_id,
        kind=ChannelKind(row.kind),
        title=row.title,
        member_user_id=row.member_user_id,
        contact_id=row.contact_id,
        handler_user_id=row.handler_user_id,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
    )


def source_entity(row: ChannelSourceModel) -> Source:
    return Source(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        surface_id=row.surface_id,
        platform=row.platform,
        kind=SourceKind(row.kind),
        external_channel_id=row.external_channel_id,
        external_user_id=row.external_user_id,
        title=row.title,
        last_inbound_at=row.last_inbound_at,
        delivery_ref=row.delivery_ref,
        owner_user_id=row.owner_user_id,
        answers_outsiders=row.answers_outsiders,
        shared_externally=row.shared_externally,
        invite_link=row.invite_link,
        request_id=row.request_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _principal(user_id: UUID | None, contact_id: UUID | None) -> Principal | None:
    if user_id is not None:
        return Principal.member(user_id)
    if contact_id is not None:
        return Principal.contact(contact_id)
    return None


def _message(row: ChannelMessageModel) -> ChannelMessage:
    return ChannelMessage(
        id=row.id,
        channel_id=row.channel_id,
        sender=Sender(
            kind=SenderKind(row.sender_kind),
            user_id=row.sender_user_id,
            contact_id=row.sender_contact_id,
            agent_id=row.sender_agent_id,
            on_behalf_of=_principal(row.on_behalf_user_id, row.on_behalf_contact_id),
            display_name=row.sender_name,
        ),
        text=row.body,
        created_at=row.created_at,
        source_id=row.source_id,
        members_only=row.members_only,
        conversation_id=row.conversation_id,
        external_message_id=row.external_message_id,
        external_thread_id=row.external_thread_id,
        reply_ref=row.reply_ref,
    )


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- channels -------------------------------------------------------------

    async def get(self, channel_id: UUID) -> Channel | None:
        row = await self.session.get(ChannelModel, channel_id)
        return _channel(row) if row else None

    async def direct(self, *, pod_id: UUID, user_id: UUID) -> Channel:
        """The member's DIRECT channel in the pod, made if there is none."""
        return await self._one_of_a_kind(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind == ChannelKind.DIRECT.value,
            ChannelModel.member_user_id == user_id,
            make=lambda: ChannelModel(
                pod_id=pod_id, kind=ChannelKind.DIRECT.value, member_user_id=user_id
            ),
        )

    async def for_contact(self, *, pod_id: UUID, contact_id: UUID) -> Channel:
        """The contact's CONTACT channel in the pod, made if there is none."""
        return await self._one_of_a_kind(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind == ChannelKind.CONTACT.value,
            ChannelModel.contact_id == contact_id,
            make=lambda: ChannelModel(
                pod_id=pod_id, kind=ChannelKind.CONTACT.value, contact_id=contact_id
            ),
        )

    async def open_group(self, *, pod_id: UUID, title: str | None) -> Channel:
        row = ChannelModel(pod_id=pod_id, kind=ChannelKind.GROUP.value, title=title)
        self.session.add(row)
        await self.session.flush()
        return _channel(row)

    async def _one_of_a_kind(
        self, *where: ColumnElement[bool], make: Callable[[], ChannelModel]
    ) -> Channel:
        statement = select(ChannelModel).where(*where)
        found = await self.session.scalar(statement)
        if found is not None:
            return _channel(found)
        try:
            async with self.session.begin_nested():
                row = make()
                self.session.add(row)
                await self.session.flush()
                return _channel(row)
        except IntegrityError:
            winner = await self.session.scalar(statement)
            if winner is None:
                raise
            return _channel(winner)

    async def set_handler(self, channel_id: UUID, user_id: UUID | None) -> None:
        await self.session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel_id)
            .values(handler_user_id=user_id)
        )

    async def list_for_member(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        kinds: tuple[ChannelKind, ...],
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Channel]:
        """The channels a member of the pod sees, most recently active first.

        Their own DIRECT channel, and every CONTACT and GROUP channel: the
        pod's contacts and groups are every member's to look after.
        """
        statement = select(ChannelModel).where(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind.in_([kind.value for kind in kinds]),
            or_(
                ChannelModel.kind != ChannelKind.DIRECT.value,
                ChannelModel.member_user_id == user_id,
            ),
        )
        activity = func.coalesce(ChannelModel.last_message_at, ChannelModel.created_at)
        if before is not None:
            at, channel_id = before
            statement = statement.where(
                or_(activity < at, and_(activity == at, ChannelModel.id < channel_id))
            )
        rows = await self.session.scalars(
            statement.order_by(activity.desc(), ChannelModel.id.desc()).limit(
                max(1, min(limit, MAX_LINES))
            )
        )
        return [_channel(row) for row in rows]

    async def attach_agent(self, *, channel_id: UUID, agent_id: UUID) -> None:
        await self.session.execute(
            insert(ChannelAgentModel)
            .values(channel_id=channel_id, agent_id=agent_id)
            .on_conflict_do_nothing()
        )

    async def fold_contact(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> None:
        """A contact turned out to be another: their channel becomes the survivor's.

        Moved rather than copied -- sources, lines and people -- and the merged
        contact's channel goes once it holds nothing. A conversation that named
        it loses the pointer and is placed again on its next message.
        """
        merged = await self.session.scalar(
            select(ChannelModel).where(
                ChannelModel.pod_id == pod_id,
                ChannelModel.kind == ChannelKind.CONTACT.value,
                ChannelModel.contact_id == merged_id,
            )
        )
        await self._rename_contact(merged_id=merged_id, survivor_id=survivor_id)
        if merged is None:
            return
        survivor = await self.for_contact(pod_id=pod_id, contact_id=survivor_id)
        for table in (ChannelSourceModel, ChannelMessageModel):
            await self.session.execute(
                update(table)
                .where(table.channel_id == merged.id)
                .values(channel_id=survivor.id)
            )
        await self.session.execute(
            delete(ChannelParticipantModel).where(
                ChannelParticipantModel.channel_id == merged.id
            )
        )
        await self.join(channel=survivor, principal=Principal.contact(survivor_id))
        await self.session.delete(merged)
        await self.session.flush()

    async def _rename_contact(self, *, merged_id: UUID, survivor_id: UUID) -> None:
        """Lines and seats that name the merged contact name the survivor."""
        await self.session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.sender_contact_id == merged_id)
            .values(sender_contact_id=survivor_id)
        )
        await self.session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.on_behalf_contact_id == merged_id)
            .values(on_behalf_contact_id=survivor_id)
        )
        # A seat both already hold in one channel keeps the survivor's.
        held = select(ChannelParticipantModel.channel_id).where(
            ChannelParticipantModel.contact_id == survivor_id
        )
        await self.session.execute(
            delete(ChannelParticipantModel).where(
                ChannelParticipantModel.contact_id == merged_id,
                ChannelParticipantModel.channel_id.in_(held),
            )
        )
        await self.session.execute(
            update(ChannelParticipantModel)
            .where(ChannelParticipantModel.contact_id == merged_id)
            .values(contact_id=survivor_id)
        )

    # -- sources --------------------------------------------------------------

    async def find_source(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_user_id: str | None,
    ) -> Source | None:
        row = await self.session.scalar(
            select(ChannelSourceModel).where(
                ChannelSourceModel.pod_id == pod_id,
                ChannelSourceModel.surface_id.is_not_distinct_from(surface_id),
                ChannelSourceModel.platform == platform,
                func.coalesce(ChannelSourceModel.external_channel_id, "")
                == (external_channel_id or ""),
                func.coalesce(ChannelSourceModel.external_user_id, "")
                == (external_user_id or ""),
            )
        )
        return source_entity(row) if row else None

    async def add_source(
        self,
        *,
        channel: Channel,
        surface_id: UUID | None,
        platform: str,
        kind: SourceKind,
        external_channel_id: str | None,
        external_user_id: str | None,
        title: str | None,
    ) -> Source | None:
        """The new source, or ``None`` when another writer added this place first."""
        try:
            async with self.session.begin_nested():
                row = ChannelSourceModel(
                    channel_id=channel.id,
                    pod_id=channel.pod_id,
                    surface_id=surface_id,
                    platform=platform,
                    kind=kind.value,
                    external_channel_id=external_channel_id,
                    external_user_id=external_user_id,
                    title=title,
                )
                self.session.add(row)
                await self.session.flush()
                return source_entity(row)
        except IntegrityError:
            return None

    async def get_source(self, source_id: UUID) -> Source | None:
        row = await self.session.get(ChannelSourceModel, source_id)
        return source_entity(row) if row else None

    async def sources_of(self, channel_id: UUID) -> list[Source]:
        rows = await self.session.scalars(
            select(ChannelSourceModel)
            .where(ChannelSourceModel.channel_id == channel_id)
            .order_by(ChannelSourceModel.created_at)
            .limit(MAX_LINES)
        )
        return [source_entity(row) for row in rows]

    # -- people ---------------------------------------------------------------

    async def join(self, *, channel: Channel, principal: Principal) -> None:
        """Make somebody a participant; nothing when they already are one."""
        await self.session.execute(
            insert(ChannelParticipantModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=principal.user_id,
                contact_id=principal.contact_id,
            )
            .on_conflict_do_nothing()
        )

    async def participants(self, channel_id: UUID) -> list[Participant]:
        rows = await self.session.scalars(
            select(ChannelParticipantModel)
            .where(ChannelParticipantModel.channel_id == channel_id)
            .order_by(ChannelParticipantModel.created_at)
            .limit(MAX_LINES)
        )
        return [
            Participant(
                channel_id=row.channel_id,
                principal=Principal(user_id=row.user_id, contact_id=row.contact_id),
                last_read_at=row.last_read_at,
            )
            for row in rows
        ]

    async def mark_read(self, *, channel: Channel, user_id: UUID) -> None:
        """The member has read the channel up to now -- joining it if they only
        ever read it, so the mark has a row to live on."""
        read_at = _now()
        await self.session.execute(
            insert(ChannelParticipantModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=user_id,
                last_read_at=read_at,
            )
            .on_conflict_do_update(
                index_elements=["channel_id", "user_id"],
                index_where=ChannelParticipantModel.user_id.is_not(None),
                set_={"last_read_at": read_at},
            )
        )

    # -- the timeline ---------------------------------------------------------

    async def append(
        self,
        *,
        channel: Channel,
        sender: Sender,
        text: str,
        source_id: UUID | None,
        members_only: bool,
        conversation_id: UUID | None,
        external_message_id: str | None,
        external_thread_id: str | None,
        reply_ref: Mapping[str, object] | None = None,
    ) -> tuple[ChannelMessage, bool]:
        """Add a line, or return the one this platform message already made.

        A platform that delivers a message twice -- a retried webhook, a
        replayed poll -- names it the same both times, and the second writes
        nothing. Says which: ``True`` only for a line written now. A line
        logged before anyone answered it -- a group's -- takes the conversation
        that answers it, and what answers it in place, the second time.
        """
        behalf = sender.on_behalf_of
        values = {
            "channel_id": channel.id,
            "pod_id": channel.pod_id,
            "source_id": source_id,
            "sender_kind": sender.kind.value,
            "sender_user_id": sender.user_id,
            "sender_contact_id": sender.contact_id,
            "sender_agent_id": sender.agent_id,
            "on_behalf_user_id": behalf.user_id if behalf else None,
            "on_behalf_contact_id": behalf.contact_id if behalf else None,
            "sender_name": sender.display_name[:255] if sender.display_name else None,
            "body": text,
            "members_only": members_only,
            "conversation_id": conversation_id,
            "external_message_id": external_message_id,
            "external_thread_id": external_thread_id,
            "reply_ref": dict(reply_ref) if reply_ref is not None else None,
        }
        statement = insert(ChannelMessageModel).values(**values)
        if external_message_id is not None and source_id is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=["source_id", "external_message_id"],
                index_where=ChannelMessageModel.external_message_id.is_not(None),
            )
        inserted = await self.session.scalar(statement.returning(ChannelMessageModel))
        if inserted is None:
            existing = await self.session.scalar(
                select(ChannelMessageModel).where(
                    ChannelMessageModel.source_id == source_id,
                    ChannelMessageModel.external_message_id == external_message_id,
                )
            )
            if existing is None:  # pragma: no cover - the conflict named it
                raise LookupError("A duplicate line vanished")
            if existing.conversation_id is None and conversation_id is not None:
                existing.conversation_id = conversation_id
            if existing.reply_ref is None and reply_ref is not None:
                existing.reply_ref = dict(reply_ref)
            await self.session.flush()
            return _message(existing), False
        await self.session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel.id)
            .values(last_message_at=inserted.created_at)
        )
        if source_id is not None and sender.kind is not SenderKind.AGENT:
            await self.session.execute(
                update(ChannelSourceModel)
                .where(ChannelSourceModel.id == source_id)
                .values(last_inbound_at=inserted.created_at)
            )
        return _message(inserted), True

    async def recent(
        self,
        channel_id: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
        source_id: UUID | None = None,
    ) -> list[ChannelMessage]:
        """The newest lines, oldest first; ``source_id`` keeps one source's."""
        statement = select(ChannelMessageModel).where(
            ChannelMessageModel.channel_id == channel_id
        )
        if source_id is not None:
            statement = statement.where(ChannelMessageModel.source_id == source_id)
        if before is not None:
            at, message_id = before
            statement = statement.where(
                or_(
                    ChannelMessageModel.created_at < at,
                    and_(
                        ChannelMessageModel.created_at == at,
                        ChannelMessageModel.id < message_id,
                    ),
                )
            )
        rows = list(
            await self.session.scalars(
                statement.order_by(
                    ChannelMessageModel.created_at.desc(),
                    ChannelMessageModel.id.desc(),
                ).limit(max(1, min(limit, MAX_LINES)))
            )
        )
        return [_message(row) for row in reversed(rows)]

    async def search(
        self, *, channel_ids: list[UUID], words: str, limit: int
    ) -> list[ChannelMessage]:
        """Lines in these channels matching every word, newest first."""
        if not channel_ids or not words.strip():
            return []
        query = func.plainto_tsquery("simple", words)
        rows = await self.session.scalars(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.channel_id.in_(channel_ids),
                func.to_tsvector("simple", ChannelMessageModel.body).op("@@")(query),
            )
            .order_by(ChannelMessageModel.created_at.desc())
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return [_message(row) for row in rows]
