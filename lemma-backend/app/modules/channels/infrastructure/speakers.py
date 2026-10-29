"""Who is in channels' places: who has spoken there, and who the platform
says joined without a word yet.

Asked once for a whole list of groups: everybody who said something in each
channel, from their seats -- each seat keeps when its person last spoke and
the name they spoke under (``channel_members.last_spoke_at``, ``last_name``,
written as the line is posted) -- so the read is one statement over the
seats, never over the lines. A seat the platform made when somebody joined
(``SeatedBy.PLATFORM``) counts before they speak, after everyone who has, under
the name the platform showed them by, if any.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Principal, SeatedBy
from app.modules.channels.infrastructure.models import ChannelMemberModel

#: Ceilings on one read. A pod lists at most a few hundred groups.
MAX_SPEAKERS = 5000


class Speaker(BaseModel):
    """Somebody who said something in a channel: a member or a contact."""

    model_config = ConfigDict(frozen=True)

    channel_id: UUID
    principal: Principal
    #: The name they last spoke under, as the platform showed it.
    name: str | None
    #: None for somebody who joined and has not spoken yet.
    last_said_at: datetime | None


class SpeakerReads:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def speakers(
        self, channel_ids: Collection[UUID], *, limit: int = MAX_SPEAKERS
    ) -> list[Speaker]:
        """The people who spoke in these channels, once per channel, newest
        first -- then the ones the platform seated who have not spoken yet.

        Agents are not speakers: an agent is attached to a channel, never one
        of the people in it. Somebody taken out of the channel since is not
        in it to list.
        """
        if not channel_ids:
            return []
        seat = ChannelMemberModel
        rows = (
            await self.session.execute(
                select(
                    seat.channel_id,
                    seat.user_id,
                    seat.contact_id,
                    seat.last_name,
                    seat.last_spoke_at,
                )
                .where(
                    seat.channel_id.in_(sorted(channel_ids)),
                    seat.agent_id.is_(None),
                    or_(
                        seat.last_spoke_at.is_not(None),
                        seat.seated_by == SeatedBy.PLATFORM.value,
                    ),
                )
                .order_by(seat.last_spoke_at.desc().nulls_last(), seat.id)
                .limit(max(1, min(limit, MAX_SPEAKERS)))
            )
        ).all()
        return [
            Speaker(
                channel_id=channel_id,
                principal=(
                    Principal.member(user_id)
                    if user_id is not None
                    else Principal.contact(contact_id)
                ),
                name=name,
                last_said_at=said_at,
            )
            for channel_id, user_id, contact_id, name, said_at in rows
        ]
