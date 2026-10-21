"""Who is in a channel -- members, contacts, agents. How far they read is
``read_state``.

Every seat says why it was taken (``SeatedBy``), and a stronger reason
replaces a weaker one when the person is seated again: the platform showing
them there, then their speaking in Lemma, then anything the pod chose -- a
member adding them, their joining, their making it. Only a seat the platform
alone accounts for is the platform's to take back (``remove_platform_seat``).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    case,
    delete,
    func,
    literal,
    literal_column,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    Member,
    MemberRole,
    NotifyLevel,
    Principal,
    SeatedBy,
)
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
    ChannelReadStateModel,
)
from app.modules.channels.infrastructure.read_state import ReadState
from app.modules.channels.infrastructure.revisions import (
    touch_channel,
    touch_channels,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, member_entity, now

#: The most members one channel lists at once.
MAX_MEMBERS = 500

#: How much a reason to be seated says: a seat is re-seated only by a
#: stronger one. Equal at the top -- each is the pod's own choice.
_STRENGTH = {
    SeatedBy.PLATFORM: 0,
    SeatedBy.SPOKE: 1,
    SeatedBy.ADDED: 2,
    SeatedBy.JOINED: 2,
    SeatedBy.CREATOR: 2,
}


def _strength(
    column: ColumnElement[str] | InstrumentedAttribute[str],
) -> ColumnElement[int]:
    return case(
        {reason.value: rank for reason, rank in _STRENGTH.items()}, value=column
    )


class MemberRepository(ReadState):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_person(
        self,
        channel: Channel,
        principal: Principal,
        *,
        seated_by: SeatedBy,
        role: MemberRole | None = None,
        added_by_user_id: UUID | None = None,
        spoke: tuple[datetime, str | None] | None = None,
    ) -> bool:
        """Put a member or a contact in the channel, for ``seated_by``. Already
        in it, the seat keeps its role and takes the stronger reason; ``spoke``
        (when, and the name they spoke under) is noted either way. ``True``
        only for a seat made now."""
        seat = ChannelMemberModel
        values: dict[str, object] = {
            "channel_id": channel.id,
            "pod_id": channel.pod_id,
            "user_id": principal.user_id,
            "contact_id": principal.contact_id,
            "role": (
                role
                or (MemberRole.GUEST if principal.is_contact else MemberRole.MEMBER)
            ).value,
            "notify": NotifyLevel.default_in(channel.kind).value,
            "added_by_user_id": added_by_user_id,
            "seated_by": seated_by.value,
        }
        if spoke is not None:
            values["last_spoke_at"], values["last_name"] = spoke
        statement = insert(seat).values(**values)
        stronger = _strength(statement.excluded.seated_by) > _strength(seat.seated_by)
        again: dict[str, object] = {
            "seated_by": case(
                (stronger, statement.excluded.seated_by), else_=seat.seated_by
            ),
            "added_by_user_id": case(
                (stronger, statement.excluded.added_by_user_id),
                else_=seat.added_by_user_id,
            ),
        }
        if spoke is not None:
            again["last_spoke_at"] = func.greatest(
                seat.last_spoke_at, statement.excluded.last_spoke_at
            )
            again["last_name"] = func.coalesce(
                statement.excluded.last_name, seat.last_name
            )
        column = seat.user_id if principal.user_id is not None else seat.contact_id
        seated = await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[seat.channel_id, column],
                index_where=column.is_not(None),
                set_=again,
            ).returning(literal_column("xmax = 0"))
        )
        made = bool(seated.scalar_one())
        if made:
            await touch_channel(self.session, channel.id)
            if principal.user_id is not None:
                await self._read_from_now(channel, principal.user_id)
        return made

    async def _read_from_now(self, channel: Channel, user_id: UUID) -> None:
        """A member new to a channel starts reading at its newest line: what
        was said before they came is history, not news. A line that named
        them before stays unread -- it is theirs to read."""
        state = ChannelReadStateModel
        head = (
            select(ChannelModel.last_seq, ChannelModel.replies)
            .where(ChannelModel.id == channel.id)
            .subquery()
        )
        statement = insert(state).from_select(
            ["channel_id", "pod_id", "user_id", "last_read_seq", "read_replies"],
            select(
                literal(channel.id),
                literal(channel.pod_id),
                literal(user_id),
                head.c.last_seq,
                head.c.replies,
            ),
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "channel_id"],
                set_={
                    "last_read_seq": statement.excluded.last_read_seq,
                    "read_replies": statement.excluded.read_replies,
                    "updated_at": func.now(),
                },
                where=state.last_read_seq < statement.excluded.last_read_seq,
            )
        )

    async def add_agent(
        self,
        channel: Channel,
        agent_id: UUID,
        *,
        answers: AgentAnswers,
        seated_by: SeatedBy = SeatedBy.ADDED,
        added_by_user_id: UUID | None = None,
    ) -> None:
        """Put an agent in the channel, or change when it answers there.
        Agents are only ever put in a channel by somebody (``ADDED``) or with
        it (``CREATOR``)."""
        await touch_channel(self.session, channel.id)
        await self.session.execute(
            insert(ChannelMemberModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                agent_id=agent_id,
                role=MemberRole.MEMBER.value,
                answers=answers.value,
                added_by_user_id=added_by_user_id,
                seated_by=seated_by.value,
            )
            .on_conflict_do_update(
                index_elements=["channel_id", "agent_id"],
                index_where=ChannelMemberModel.agent_id.is_not(None),
                set_={"answers": answers.value},
            )
        )

    async def remove(
        self,
        channel_id: UUID,
        *,
        user_id: UUID | None = None,
        contact_id: UUID | None = None,
        agent_id: UUID | None = None,
    ) -> bool:
        member = ChannelMemberModel
        statement = delete(member).where(member.channel_id == channel_id)
        if user_id is not None:
            statement = statement.where(member.user_id == user_id)
        elif contact_id is not None:
            statement = statement.where(member.contact_id == contact_id)
        elif agent_id is not None:
            statement = statement.where(member.agent_id == agent_id)
        else:
            return False
        # The channel before the seat, the order a new line takes them in.
        await touch_channel(self.session, channel_id)
        result = await self.session.execute(statement.returning(member.id))
        return result.first() is not None

    async def set_role(self, channel_id: UUID, user_id: UUID, role: MemberRole) -> None:
        await touch_channel(self.session, channel_id)
        await self.session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id == user_id,
            )
            .values(role=role.value, updated_at=now())
        )

    async def set_prefs(
        self,
        channel_id: UUID,
        user_id: UUID,
        values: dict[str, object],
    ) -> bool:
        """Change a member's own notify, notify_via, muted_until; only theirs.
        ``False`` when they are not in the channel."""
        allowed = {"notify", "notify_via", "muted_until"}
        if set(values) - allowed:
            raise ValueError(f"Not a member setting: {sorted(set(values) - allowed)}")
        if not values:
            return await self.of_user(channel_id, user_id) is not None
        result = await self.session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id == user_id,
            )
            .values(**values, updated_at=now())
            .returning(ChannelMemberModel.id)
        )
        return result.first() is not None

    # -- reading ------------------------------------------------------------------

    async def of_channel(
        self, channel_id: UUID, *, limit: int = MAX_MEMBERS
    ) -> list[Member]:
        rows = await self.session.scalars(
            select(ChannelMemberModel)
            .where(ChannelMemberModel.channel_id == channel_id)
            .order_by(ChannelMemberModel.created_at, ChannelMemberModel.id)
            .limit(max(1, min(limit, MAX_MEMBERS)))
        )
        return [member_entity(row) for row in rows]

    async def page(
        self,
        channel_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[Member]:
        """Who is in the channel, in the order they joined, from ``after``
        (a seat's join time and id)."""
        member = ChannelMemberModel
        statement = select(member).where(member.channel_id == channel_id)
        if after is not None:
            joined, seat_id = after
            statement = statement.where(
                tuple_(member.created_at, member.id)
                > tuple_(literal(joined), literal(seat_id))
            )
        rows = await self.session.scalars(
            statement.order_by(member.created_at, member.id).limit(
                max(1, min(limit, MAX_MEMBERS))
            )
        )
        return [member_entity(row) for row in rows]

    async def of_user(self, channel_id: UUID, user_id: UUID) -> Member | None:
        row = await self.session.scalar(
            select(ChannelMemberModel).where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id == user_id,
            )
        )
        return member_entity(row) if row else None

    async def of_principal(
        self, channel_id: UUID, principal: Principal
    ) -> Member | None:
        member = ChannelMemberModel
        column = member.user_id if principal.user_id else member.contact_id
        row = await self.session.scalar(
            select(member).where(
                member.channel_id == channel_id,
                column == (principal.user_id or principal.contact_id),
            )
        )
        return member_entity(row) if row else None

    async def someone_else(
        self, channel_id: UUID, user_id: UUID, *, role: MemberRole | None = None
    ) -> UUID | None:
        """The member other than ``user_id`` longest in the channel -- in
        ``role``, when named -- or ``None``. The channel's row is locked
        first, so two members leaving it at once cannot each count on the
        other."""
        await self.session.execute(
            select(ChannelModel.id)
            .where(ChannelModel.id == channel_id)
            .with_for_update()
        )
        seat = ChannelMemberModel
        statement = select(seat.user_id).where(
            seat.channel_id == channel_id,
            seat.user_id.is_not(None),
            seat.user_id != user_id,
        )
        if role is not None:
            statement = statement.where(seat.role == role.value)
        return await self.session.scalar(
            statement.order_by(seat.created_at, seat.id).limit(1)
        )

    async def users_in(self, channel_id: UUID, user_ids: Collection[UUID]) -> set[UUID]:
        if not user_ids:
            return set()
        rows = await self.session.scalars(
            select(ChannelMemberModel.user_id).where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id.in_(sorted(user_ids)),
            )
        )
        return {row for row in rows if row is not None}

    async def channels_of(
        self, user_id: UUID, channel_ids: Collection[UUID]
    ) -> set[UUID]:
        """Which of ``channel_ids`` the member is in."""
        if not channel_ids:
            return set()
        rows = await self.session.scalars(
            select(ChannelMemberModel.channel_id).where(
                ChannelMemberModel.user_id == user_id,
                ChannelMemberModel.channel_id.in_(sorted(channel_ids)),
            )
        )
        return set(rows)

    async def agents(self, channel_id: UUID) -> list[Member]:
        rows = await self.session.scalars(
            select(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.agent_id.is_not(None),
            )
            .limit(MAX_LINES)
        )
        return [member_entity(row) for row in rows]

    async def people_to_tell(
        self,
        channel_id: UUID,
        *,
        after: tuple[datetime, UUID] | None = None,
        limit: int = MAX_MEMBERS,
    ) -> list[Member]:
        """One page of the members of the pod in the channel who hear of its
        lines at all, in the order they joined, from ``after`` (a seat's
        ``joined_at`` and ``seat_id``) -- along ``ix_channel_members_channel``."""
        seat = ChannelMemberModel
        statement = select(seat).where(
            seat.channel_id == channel_id,
            seat.user_id.is_not(None),
            seat.notify != NotifyLevel.NONE.value,
        )
        if after is not None:
            joined, seat_id = after
            statement = statement.where(
                tuple_(seat.created_at, seat.id)
                > tuple_(literal(joined), literal(seat_id))
            )
        rows = await self.session.scalars(
            statement.order_by(seat.created_at, seat.id).limit(
                max(1, min(limit, MAX_MEMBERS))
            )
        )
        return [member_entity(row) for row in rows]

    async def remove_from_pod(self, *, pod_id: UUID, user_id: UUID) -> None:
        """A member who left the pod is in none of its channels."""
        seats = (
            ChannelMemberModel.pod_id == pod_id,
            ChannelMemberModel.user_id == user_id,
        )
        # The channels first (``revisions``): their subscribers re-check who
        # may still read them.
        await touch_channels(
            self.session, select(ChannelMemberModel.channel_id).where(*seats)
        )
        await self.session.execute(delete(ChannelMemberModel).where(*seats))

    # -- read state ---------------------------------------------------------------

    async def remove_platform_seat(
        self, channel_id: UUID, principal: Principal
    ) -> bool:
        """Take out a person the platform says left the place this channel
        mirrors -- when the platform alone is what put them here (a
        ``PLATFORM`` seat). Somebody who spoke here in Lemma, a member added,
        or who joined -- an admin on request -- is the pod's choice and stays,
        whoever added them and whether or not that member is still here."""
        member = ChannelMemberModel
        who = (
            member.user_id == principal.user_id
            if principal.user_id is not None
            else member.contact_id == principal.contact_id
        )
        seated = await self.session.scalar(
            select(member.id).where(
                member.channel_id == channel_id,
                who,
                member.seated_by == SeatedBy.PLATFORM.value,
            )
        )
        if seated is None:
            return False
        # The channel before the seat, the order a new line takes them in.
        await touch_channel(self.session, channel_id)
        await self.session.execute(delete(member).where(member.id == seated))
        return True
