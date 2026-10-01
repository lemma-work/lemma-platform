"""Which surface a conversation arrived through, for a reader outside this module.

A conversation started from Slack, Teams, WhatsApp or a mailbox has a link row
pointing back at the surface that opened it. Anything asking "did this answer go
out over a surface, and which one" wants the surface id and nothing else.

The id rather than the link row, for the same reason ``pod/contracts/members.py``
publishes a member id: handing back the entity invites a second module to start
reading the external thread, channel and user identifiers off it, none of which
are anyone else's to hold.

A submodule rather than ``contracts/__init__``, which is a leaf: this reaches the
repository layer.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)


async def surface_id_for_conversation(uow, conversation_id: UUID) -> UUID | None:
    """The surface this conversation came in on, or ``None`` if it did not."""
    link = await SurfaceConversationLinkRepository(uow).get_by_conversation_id(
        conversation_id
    )
    return link.surface_id if link is not None else None


async def conversation_answers_outsiders(uow, conversation_id: UUID) -> bool:
    """Whether routing opened this conversation to answer people outside the pod.

    The second, independent half of "is this a stranger's run": the agent module
    stamps the fact on the conversation's metadata, and this asks the link that
    routing wrote. Either one saying so makes the run a stranger's, so a
    conversation that lost its flag still never runs with its owner's authority.
    """
    return await SurfaceConversationLinkRepository(uow).is_outsiders_thread(
        conversation_id
    )


__all__ = ["conversation_answers_outsiders", "surface_id_for_conversation"]
