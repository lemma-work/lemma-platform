"""Channels: made, found, changed, listed for whoever is looking."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    and_,
    column,
    delete,
    or_,
    select,
    table,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ContactStatus,
    MemberRole,
    NotifyLevel,
    SeatedBy,
    Visibility,
)
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.cases import CaseRepository, narrowed_by_case
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
)
from app.modules.channels.infrastructure.revisions import touch_channel
from app.modules.channels.infrastructure.rows import MAX_LINES, channel_entity, now
from app.modules.identity.contracts.orm import User

#: What a caller may change on a channel through ``change``.
CHANGEABLE = frozenset(
    {
        "title",
        "topic",
        "visibility",
        "archived_at",
        "answers_shared",
        "agents_answer",
        "hand_back_after_hours",
    }
)


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, channel_id: UUID) -> Channel | None:
        row = await self.session.get(ChannelModel, channel_id)
        return channel_entity(row) if row else None

    async def by_ids(self, channel_ids: Collection[UUID]) -> dict[UUID, Channel]:
        if not channel_ids:
            return {}
        rows = await self.session.scalars(
            select(ChannelModel).where(
                ChannelModel.id.in_(sorted(channel_ids)[:MAX_LINES])
            )
        )
        return {row.id: channel_entity(row) for row in rows}

    async def in_pod(self, *, pod_id: UUID, channel_id: UUID) -> Channel | None:
        row = await self.session.scalar(
            select(ChannelModel).where(
                ChannelModel.id == channel_id, ChannelModel.pod_id == pod_id
            )
        )
        return channel_entity(row) if row else None

    async def lock(self, channel_id: UUID) -> ChannelModel | None:
        """The channel's row, held until the transaction ends: the timeline's
        next number is taken under it."""
        return await self.session.scalar(
            select(ChannelModel).where(ChannelModel.id == channel_id).with_for_update()
        )

    # -- the channels a person has --------------------------------------------

    async def direct(self, *, pod_id: UUID, user_id: UUID) -> Channel:
        """The member's own channel in the pod, made with them in it."""
        channel = await self._one_of_a_kind(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind == ChannelKind.DIRECT.value,
            ChannelModel.member_user_id == user_id,
            make=lambda: ChannelModel(
                pod_id=pod_id,
                kind=ChannelKind.DIRECT.value,
                visibility=Visibility.PRIVATE.value,
                member_user_id=user_id,
            ),
        )
        await self._seat(channel, user_id=user_id, role=MemberRole.OWNER)
        return channel

    async def for_contact(self, *, pod_id: UUID, contact_id: UUID) -> Channel:
        """The contact's channel in the pod, made with them in it and its
        first case open."""
        channel = await self._one_of_a_kind(
            ChannelModel.kind == ChannelKind.CONTACT.value,
            ChannelModel.contact_id == contact_id,
            make=lambda: ChannelModel(
                pod_id=pod_id,
                kind=ChannelKind.CONTACT.value,
                visibility=Visibility.PRIVATE.value,
                contact_id=contact_id,
            ),
            then=CaseRepository(self.session).open_first,
        )
        await self._seat(channel, contact_id=contact_id, role=MemberRole.GUEST)
        return channel

    async def open_group(
        self,
        *,
        pod_id: UUID,
        title: str | None,
        visibility: Visibility,
        topic: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> Channel:
        return await self._open_group(
            ChannelModel(
                pod_id=pod_id,
                kind=ChannelKind.GROUP.value,
                visibility=visibility.value,
                title=title,
                topic=topic,
                created_by_user_id=created_by_user_id,
            )
        )

    async def open_group_once(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        title: str | None,
        visibility: Visibility,
        topic: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> tuple[Channel, bool]:
        """A group with this id: made now (``True``), or the one a retry of
        the same request already made (``False``)."""
        found = await self.get(channel_id)
        if found is not None:
            return found, False
        try:
            async with self.session.begin_nested():
                made = await self._open_group(
                    ChannelModel(
                        id=channel_id,
                        pod_id=pod_id,
                        kind=ChannelKind.GROUP.value,
                        visibility=visibility.value,
                        title=title,
                        topic=topic,
                        created_by_user_id=created_by_user_id,
                    )
                )
            return made, True
        except IntegrityError:
            winner = await self.get(channel_id)
            if winner is None:
                raise
            return winner, False

    async def _open_group(self, row: ChannelModel) -> Channel:
        self.session.add(row)
        await self.session.flush()
        channel = channel_entity(row)
        if row.created_by_user_id is not None:
            await self._seat(
                channel, user_id=row.created_by_user_id, role=MemberRole.OWNER
            )
        return channel

    async def _one_of_a_kind(
        self,
        *where: ColumnElement[bool],
        make: Callable[[], ChannelModel],
        then: Callable[[ChannelModel], Awaitable[None]] | None = None,
    ) -> Channel:
        statement = select(ChannelModel).where(*where)
        found = await self.session.scalar(statement)
        if found is not None:
            return channel_entity(found)
        try:
            async with self.session.begin_nested():
                row = make()
                self.session.add(row)
                await self.session.flush()
                if then is not None:
                    await then(row)
                return channel_entity(row)
        except IntegrityError:
            winner = await self.session.scalar(statement)
            if winner is None:
                raise
            return channel_entity(winner)

    async def _seat(
        self,
        channel: Channel,
        *,
        role: MemberRole,
        user_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> None:
        await self.session.execute(
            insert(ChannelMemberModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=user_id,
                contact_id=contact_id,
                role=role.value,
                notify=NotifyLevel.default_in(channel.kind).value,
                seated_by=SeatedBy.CREATOR.value,
            )
            .on_conflict_do_nothing()
        )

    # -- changing one ---------------------------------------------------------

    async def change(self, channel_id: UUID, values: Mapping[str, object]) -> None:
        """Set exactly these fields; a caller decides absent from cleared."""
        unknown = set(values) - CHANGEABLE
        if unknown:
            raise ValueError(f"Not a channel field: {sorted(unknown)}")
        if values:
            await touch_channel(self.session, channel_id)
            await self.session.execute(
                update(ChannelModel)
                .where(ChannelModel.id == channel_id)
                .values(**values, updated_at=now())
            )

    async def delete(self, channel_id: UUID) -> None:
        # Its live subscribers hear, and find it gone.
        await touch_channel(self.session, channel_id)
        await self.session.execute(
            delete(ChannelModel).where(ChannelModel.id == channel_id)
        )

    # -- listing ----------------------------------------------------------------

    async def list_for(
        self,
        viewer: Viewer,
        *,
        kind: ChannelKind | None = None,
        visibility: Visibility | None = None,
        titled: str | None = None,
        archived: bool = False,
        contact_id: UUID | None = None,
        assignee_user_id: UUID | None = None,
        unassigned: bool = False,
        status: ContactStatus | None = None,
        needs_person: bool = False,
        joined_only: bool = False,
        channel_ids: Collection[UUID] | None = None,
        every_group: bool = False,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[Channel]:
        """The channels ``viewer`` may see, most recently active first --
        among ``channel_ids``, when given. ``titled``: named by every one of
        these words, in any order and case -- by title, or by whose own
        channel it is. ``every_group``: the pod's private groups too, seen
        or not (a pod admin's view of a bot's groups)."""
        if channel_ids is not None and not channel_ids:
            return []
        channel = ChannelModel
        shown = (
            or_(
                visible_to(viewer),
                and_(
                    channel.pod_id == viewer.pod_id,
                    channel.kind == ChannelKind.GROUP.value,
                ),
            )
            if every_group
            else visible_to(viewer)
        )
        statement = select(channel).where(
            shown,
            channel.archived_at.is_not(None)
            if archived
            else channel.archived_at.is_(None),
        )
        statement = narrowed_by_case(
            statement.where(
                *_fields_match(
                    kind=kind,
                    visibility=visibility,
                    titled=titled,
                    contact_id=contact_id,
                )
            ),
            pod_id=viewer.pod_id,
            assignee_user_id=assignee_user_id,
            unassigned=unassigned,
            status=status,
            needs_person=needs_person,
        )
        if channel_ids is not None:
            statement = statement.where(channel.id.in_(list(channel_ids)))
        if joined_only:
            member = ChannelMemberModel
            statement = statement.where(
                channel.id.in_(
                    select(member.channel_id).where(member.user_id == viewer.user_id)
                )
            )
        if before is not None:
            # A row comparison, so the page is a range of
            # ``ix_channels_pod_activity`` walked from the cursor; the same
            # test spelled with ``OR`` reads every channel older than it.
            at, channel_id = before
            statement = statement.where(
                tuple_(channel.last_line_at, channel.id) < tuple_(at, channel_id)
            )
        rows = await self.session.scalars(
            statement.order_by(channel.last_line_at.desc(), channel.id.desc()).limit(
                max(1, min(limit, MAX_LINES))
            )
        )
        return [channel_entity(row) for row in rows]


def _like_literal(words: str) -> str:
    """``words`` matched as typed: LIKE's own wildcards are escaped."""
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: The most words of a list's ``q`` that narrow it.
_MAX_WORDS = 8

#: A pod's contacts, by the name the pod gave them: a table clause rather than
#: the contacts module's model, which this module may not import.
_CONTACTS = table("contacts", column("id"), column("display_name"))


def _named_by(word: str) -> ColumnElement[bool]:
    """The channel's name holds ``word``, in any case: its title, or the name
    of whose own channel it is -- the member's (or their email), the
    contact's."""
    channel = ChannelModel
    like = f"%{_like_literal(word)}%"
    member = select(User.id).where(
        User.id == channel.member_user_id,
        or_(
            User.first_name.ilike(like, escape="\\"),
            User.last_name.ilike(like, escape="\\"),
            User.email.ilike(like, escape="\\"),
        ),
    )
    contact = select(_CONTACTS.c.id).where(
        _CONTACTS.c.id == channel.contact_id,
        _CONTACTS.c.display_name.ilike(like, escape="\\"),
    )
    return or_(
        channel.title.ilike(like, escape="\\"),
        and_(channel.member_user_id.is_not(None), member.exists()),
        and_(channel.contact_id.is_not(None), contact.exists()),
    )


def _fields_match(
    *,
    kind: ChannelKind | None,
    visibility: Visibility | None,
    titled: str | None,
    contact_id: UUID | None,
) -> list[ColumnElement[bool]]:
    """A channel list's narrowing by the channel's own fields; none for a
    field not asked about."""
    channel = ChannelModel
    conditions: list[ColumnElement[bool]] = []
    if kind is not None:
        conditions.append(channel.kind == kind.value)
    if visibility is not None:
        conditions.append(channel.visibility == visibility.value)
    if titled is not None:
        conditions += [_named_by(word) for word in titled.split()[:_MAX_WORDS]]
    if contact_id is not None:
        conditions.append(channel.contact_id == contact_id)
    return conditions
