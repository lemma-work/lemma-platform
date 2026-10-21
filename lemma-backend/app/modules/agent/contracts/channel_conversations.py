"""A person's conversation with an agent, inside a channel.

A conversation is one agent working for one person -- a member, or a contact
-- and in a channel there is one per agent and person: however many threads,
numbers or days the person writes across, their messages there continue it.
The surface that hears a message asks for that conversation here, and places a
conversation it opened in its channel.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Select, func, select, text, update

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel


async def conversation_in_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    user_id: UUID,
    contact_id: UUID | None,
) -> UUID | None:
    """The person's live conversation with this agent in the channel, if any.

    The person is the contact when ``contact_id`` is set -- whoever looks after
    them -- and otherwise the member ``user_id`` names. An archived
    conversation, or a sub-agent's, is never continued.
    """
    return await uow.session.scalar(
        conversation_in_channel_query(
            channel_id=channel_id,
            pod_id=pod_id,
            agent_id=agent_id,
            person_id=contact_id if contact_id is not None else user_id,
        )
    )


def conversation_in_channel_query(
    *, channel_id: UUID, pod_id: UUID, agent_id: UUID | None, person_id: UUID
) -> Select[tuple[UUID]]:
    """Spelled as ``uq_agent_conversations_channel_person`` spells its columns
    and predicate, so that index answers it: ``coalesce`` for the agent and
    the person, and ``NOT is_archived`` -- ``is_archived IS false`` is not
    taken to imply the index's predicate. A contact's id never names a
    member, so the person's id alone tells the two apart."""
    conversation = ConversationModel
    agent = effective_agent_id(agent_id, pod_id=pod_id)
    return (
        select(conversation.id)
        .where(
            conversation.channel_id == channel_id,
            conversation.pod_id == pod_id,
            func.coalesce(conversation.agent_id, conversation.pod_id) == agent,
            func.coalesce(conversation.contact_id, conversation.user_id) == person_id,
            conversation.parent_id.is_(None),
            ~conversation.is_archived,
        )
        .limit(1)
    )


async def lock_person_in_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    person_id: UUID,
) -> None:
    """Serialise finding-or-opening one person's conversation in a channel
    until this transaction ends.

    Two first messages from one person -- on two platforms at once, or a
    burst on one -- would otherwise each find none and each open one. The
    second waits here, then finds the first's. The unique index behind it is
    the backstop, not the plan.
    """
    agent = effective_agent_id(agent_id, pod_id=pod_id)
    await uow.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"channel-person:{channel_id}:{agent}:{person_id}"},
    )


async def place_in_channel(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, channel_id: UUID
) -> None:
    """Put a conversation that has no channel yet in this one."""
    await uow.session.execute(
        update(ConversationModel)
        .where(
            ConversationModel.id == conversation_id,
            ConversationModel.channel_id.is_(None),
        )
        .values(channel_id=channel_id)
    )


async def channel_of_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> UUID | None:
    """The channel a conversation works in, or ``None``."""
    return await uow.session.scalar(
        select(ConversationModel.channel_id).where(
            ConversationModel.id == conversation_id
        )
    )


#: The most conversations one read of contacts' conversations returns.
_MAX_CONTACT_CONVERSATIONS = 2000


async def contact_conversations_in(
    uow: SqlAlchemyUnitOfWork, channel_ids: Collection[UUID]
) -> dict[UUID, list[UUID]]:
    """Each channel's conversations with contacts -- its people outside the pod."""
    if not channel_ids:
        return {}
    conversation = ConversationModel
    rows = (
        await uow.session.execute(
            select(conversation.channel_id, conversation.id)
            .where(
                conversation.channel_id.in_(sorted(channel_ids)),
                conversation.contact_id.is_not(None),
                conversation.parent_id.is_(None),
            )
            .limit(_MAX_CONTACT_CONVERSATIONS)
        )
    ).all()
    found: dict[UUID, list[UUID]] = {}
    for channel_id, conversation_id in rows:
        found.setdefault(channel_id, []).append(conversation_id)
    return found
