"""Which surface a conversation is answered through, for a reader outside this module.

A conversation said on Slack, Teams, WhatsApp or a mailbox is answered at a
place on that surface. Anything asking "did this answer go out over a surface,
and which one" wants the surface id and nothing else
(``surface_id_for_conversation``).

The agent module is the one reader that needs the place itself: a run's prompt
and tools are the platform's, and its API still shows clients the place under
the metadata keys it was once stored in. ``conversation_surface`` and
``conversation_surfaces`` are for that, and read this module's own record
(``domain/conversation_surface``).

A submodule rather than ``contracts/__init__``, which is a leaf: this reaches
the channels' places.
"""

from __future__ import annotations

from uuid import UUID

from collections.abc import Iterable

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.conversation_surface import (
    ConversationSurface,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_surface_repository import (
    ConversationSurfaces,
)
from app.modules.channels.contracts.places import reply_address


async def surface_id_for_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> UUID | None:
    """The surface this conversation is answered on, or ``None`` if none."""
    address = await reply_address(uow, conversation_id)
    return address.source.surface_id if address is not None else None


async def conversation_surface(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> ConversationSurface | None:
    """Where this conversation was last written to from, or ``None``."""
    return await ConversationSurfaces(uow).of(conversation_id)


async def conversation_surfaces(
    uow: SqlAlchemyUnitOfWork, conversation_ids: Iterable[UUID]
) -> dict[UUID, ConversationSurface]:
    """The same for many conversations; those with none are absent."""
    return await ConversationSurfaces(uow).of_many(conversation_ids)


__all__ = [
    "ConversationSurface",
    "conversation_surface",
    "conversation_surfaces",
    "surface_id_for_conversation",
]
