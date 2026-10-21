"""Reaching a member of the pod, for the modules that do: agents, the API.

A message is a line in the member's own channel; posting it plans its push,
which the delivery executor carries to the chat app they use or their email
(``services.channel_deliveries``). Each function opens its own unit of work: a
message is not part of the asking run's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class Sent:
    """The line a message became; its push follows on its own."""

    message_id: UUID
    channel_id: UUID


async def resolve_recipient(*, pod_id: UUID, reference: str) -> UUID | None:
    """A pod member id, user id, or email address → a user id in this pod."""
    from app.modules.agent_surfaces.composition import build_direct_messages

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await build_direct_messages(uow).membership.resolve_pod_recipient(
            pod_id=pod_id, reference=reference
        )


async def send_direct_message(
    *,
    pod_id: UUID,
    recipient_user_id: UUID,
    text: str,
    actor_user_id: UUID | None,
    actor_agent_id: UUID | None = None,
    agent_name: str | None = None,
    key: str | None = None,
) -> Sent:
    """A line in the recipient's own channel, said for ``actor_user_id``."""
    from app.modules.agent_surfaces.composition import build_direct_messages
    from app.modules.agent_surfaces.services.direct_messages import DirectMessage

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        line = await build_direct_messages(uow).send(
            DirectMessage(
                pod_id=pod_id,
                recipient_user_id=recipient_user_id,
                text=text,
                actor_user_id=actor_user_id,
                actor_agent_id=actor_agent_id,
                agent_name=agent_name,
                key=key,
            )
        )
        await uow.commit()
        return Sent(message_id=line.id, channel_id=line.channel_id)


async def reachable_channels(
    *,
    pod_id: UUID,
    recipients: dict[UUID, str | None],
    actor_agent_id: UUID | None,
) -> dict[UUID, list[str]]:
    """``{user_id: platforms}`` this agent can reach each person on right now.

    Degrades to "we do not know" on an infrastructure failure: an agent that
    cannot see the member list because reachability broke is worse off than
    one that sees the list without it.
    """
    from app.modules.agent_surfaces.composition import build_member_pushes

    try:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            return await build_member_pushes(uow).channels.reachable_channels(
                pod_id=pod_id, recipients=recipients, actor_agent_id=actor_agent_id
            )
    except SQLAlchemyError, OSError:
        return {}


async def tell_member(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID, text: str
) -> None:
    """A notice to one member, in their own channel: never pushed."""
    from app.modules.agent_surfaces.services.direct_messages import (
        tell_member as notice,
    )

    await notice(uow, pod_id=pod_id, user_id=user_id, text=text)


__all__ = [
    "Sent",
    "reachable_channels",
    "resolve_recipient",
    "send_direct_message",
    "tell_member",
]
