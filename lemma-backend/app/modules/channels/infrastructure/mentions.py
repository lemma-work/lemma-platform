"""Who a line names, kept to what its parts say now.

``channel_mentions`` is derived from a line's mention parts -- the people and
agents ``Naming`` resolved from them -- and nothing else writes it: a post
records them (``sync_mentions`` on a new line), an edit makes the rows say
what the new parts say (a name added is counted and told, a name taken out
stops counting), and a deleted or emptied line loses them (``drop_mentions``).
A mention's channel and number are its line's, held there by a cascading
foreign key, so a fold moving and renumbering the line moves them too.

Somebody named where they have no read number is given one, at the
channel's head as it stands before the line naming them, so their unread
mentions are counted from their own places (``inbox``) without reading every
mention they were ever given -- and, while one is unread, the channel is new
to them from the line that named them, not from its first: what was said
before is history, as it is for somebody joining.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Uuid, column, delete, literal, select, true, values
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import ChannelMessage, Principal
from app.modules.channels.infrastructure.models import (
    ChannelMentionModel,
    ChannelModel,
    ChannelReadStateModel,
)

#: Who a mention row names: a member, a contact or an agent.
_Named = tuple[UUID | None, UUID | None, UUID | None]


@dataclass(frozen=True, slots=True)
class MentionChange:
    """What a sync changed: the members newly named -- to be told -- and how
    many names went."""

    added_users: list[UUID] = field(default_factory=list)
    removed: int = 0


def _wanted(people: Sequence[Principal], agents: Sequence[UUID]) -> list[_Named]:
    named: list[_Named] = [
        (person.user_id, person.contact_id, None) for person in people
    ]
    named += [(None, None, agent_id) for agent_id in agents]
    return list(dict.fromkeys(named))


async def sync_mentions(
    session: AsyncSession,
    *,
    message: ChannelMessage,
    people: Sequence[Principal],
    agents: Sequence[UUID],
    fresh: bool = False,
) -> MentionChange:
    """Make the line's mentions exactly ``people`` and ``agents``. ``fresh``:
    the line was written just now and names nobody yet."""
    mention = ChannelMentionModel
    wanted = _wanted(people, agents)
    had: dict[_Named, UUID] = {}
    if not fresh:
        rows = await session.execute(
            select(mention.id, mention.user_id, mention.contact_id, mention.agent_id)
            .where(mention.message_id == message.id)
            .limit(len(wanted) + 64)
        )
        had = {(row[1], row[2], row[3]): row[0] for row in rows}
    gone = [row_id for named, row_id in had.items() if named not in wanted]
    new = [named for named in wanted if named not in had]
    if gone:
        await session.execute(delete(mention).where(mention.id.in_(gone)))
    if new:
        await session.execute(
            insert(mention)
            .values(
                [
                    {
                        "message_id": message.id,
                        "channel_id": message.channel_id,
                        "pod_id": message.pod_id,
                        "seq": message.seq,
                        "user_id": user_id,
                        "contact_id": contact_id,
                        "agent_id": agent_id,
                    }
                    for user_id, contact_id, agent_id in new
                ]
            )
            .on_conflict_do_nothing()
        )
    added_users = [user_id for user_id, _, _ in new if user_id is not None]
    if added_users:
        await _give_a_place(session, message, added_users)
    return MentionChange(added_users=added_users, removed=len(gone))


async def _give_a_place(
    session: AsyncSession, message: ChannelMessage, user_ids: list[UUID]
) -> None:
    """A read number for each member named, where they have none: at the
    channel's head as written. A post names people before its head is written
    (``revisions.holding`` writes it once, at the end), so that is the line
    before this one; an edit naming somebody new starts them at the newest
    line, as joining does, and the line naming them stays unread."""
    state = ChannelReadStateModel
    head = (
        select(ChannelModel.last_seq, ChannelModel.replies)
        .where(ChannelModel.id == message.channel_id)
        .subquery()
    )
    named = values(column("user_id", Uuid), name="named").data(
        [(user_id,) for user_id in sorted(set(user_ids))]
    )
    await session.execute(
        insert(state)
        .from_select(
            ["user_id", "channel_id", "pod_id", "last_read_seq", "read_replies"],
            select(
                named.c.user_id,
                literal(message.channel_id),
                literal(message.pod_id),
                head.c.last_seq,
                head.c.replies,
            ).select_from(named.join(head, true())),
        )
        .on_conflict_do_nothing(index_elements=[state.user_id, state.channel_id])
    )


async def drop_mentions(session: AsyncSession, message_ids: Collection[UUID]) -> None:
    """The lines say nothing any more -- deleted, or their writer forgotten --
    so they name nobody."""
    if not message_ids:
        return
    await session.execute(
        delete(ChannelMentionModel).where(
            ChannelMentionModel.message_id.in_(sorted(set(message_ids)))
        )
    )


__all__ = ["MentionChange", "drop_mentions", "sync_mentions"]
