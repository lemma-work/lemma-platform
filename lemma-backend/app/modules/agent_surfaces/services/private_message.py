"""A word for the person a run is working for, and never for the group.

The ``surface_send_message`` tool exists so a run can reach the person it is
working for mid-task rather than waiting for its final reply, and it promises
that person alone. Delivering it to the conversation keeps that promise
everywhere the conversation is one person's -- which is everywhere but a group.

In a group the conversation is one member's but its address is the group, so the
message would be said in front of everybody in it. It goes to that member's own
chat with the bot instead: the only thread a message meant for one person is
ever answered to. A member who has never had one is not reached at all, because
a bot cannot open a chat and the group is not a fallback -- the run's reply is
the group's answer, and this is the aside.

The other direction from :class:`SurfaceEgress`, which answers the thread that
was spoken in; the sibling of :class:`MemberReach`, which reaches a named person
on a named surface. This one has a conversation and wants the person in it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress

logger = get_logger(__name__)

#: The conversation kind of a chat several people read (a group, a channel).
_GROUP_CONVERSATION_KIND = "CHANNEL"


class PrivateMessage:
    """The current conversation's user, privately, on their surface."""

    def __init__(self, *, egress: SurfaceEgress) -> None:
        self.egress = egress

    async def send(self, *, conversation_id: UUID, message: str) -> bool:
        """Deliver to this conversation's user, or say that nobody was reached.

        ``False`` is not "the surface is down": it is also the answer for a
        group member with no private chat to receive it in, which is why the
        caller has to read it rather than assume the message went somewhere.
        """
        target = await self.egress.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        if target.link.conversation_kind != _GROUP_CONVERSATION_KIND:
            return await self.egress.send_agent_message_for_conversation(
                conversation_id=conversation_id, message=message
            )
        private = await self.egress.delivery.private_thread_for(target)
        if private is None:
            logger.info(
                "agent_surfaces.private_message.no_private_thread.observed",
                conversation_id=str(conversation_id),
                platform=target.surface.surface_type.value,
            )
            return False
        return await self.egress.send_agent_message_for_conversation(
            conversation_id=private.conversation_id, message=message
        )
