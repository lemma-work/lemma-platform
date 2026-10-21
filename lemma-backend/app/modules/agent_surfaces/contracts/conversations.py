"""Which surface a conversation is answered through, for a reader outside this module.

A conversation said on Slack, Teams, WhatsApp or a mailbox is answered at a
place on that surface. Anything asking "did this answer go out over a surface,
and which one" wants the surface id and nothing else.

The id rather than the place, for the same reason ``pod/contracts/members.py``
publishes a member id: handing back the entity invites a second module to start
reading the external thread, channel and user identifiers off it, none of which
are anyone else's to hold.

A submodule rather than ``contracts/__init__``, which is a leaf: this reaches
the channels' places.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts.places import reply_address


async def surface_id_for_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> UUID | None:
    """The surface this conversation is answered on, or ``None`` if none."""
    address = await reply_address(uow, conversation_id)
    return address.source.surface_id if address is not None else None


__all__ = [
    "surface_id_for_conversation",
]
