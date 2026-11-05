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

An email thread with other people on it is a group as well, but one whose
thread can still be answered privately: the aside goes to the sender on the same
thread, without the copies a reply-all would carry.

The other direction from :class:`SurfaceEgress`, which answers the thread that
was spoken in; the sibling of :class:`MemberReach`, which reaches a named person
on a named surface. This one has a conversation and wants the person in it.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress

logger = get_logger(__name__)

#: The conversation kind of a chat several people read (a group, a channel).


class PrivateSend(StrEnum):
    """What became of a word for one person.

    Two ways of not sending are kept apart because the run is told different
    things: a member with no private chat will never be reached this way, and
    the run should say it in its reply; a send that failed says nothing about
    whether they have one.
    """

    SENT = "sent"
    NO_PRIVATE_CHAT = "no_private_chat"
    NOT_DELIVERED = "not_delivered"


def _outcome(delivered: bool) -> PrivateSend:
    return PrivateSend.SENT if delivered else PrivateSend.NOT_DELIVERED


class PrivateMessage:
    """The current conversation's user, privately, on their surface."""

    def __init__(self, *, egress: SurfaceEgress) -> None:
        self.egress = egress

    async def send(self, *, conversation_id: UUID, message: str) -> PrivateSend:
        """Deliver to this conversation's user, or say why nobody was reached.

        A group member with no private chat to receive it in is
        ``NO_PRIVATE_CHAT``, apart from a send that failed: the caller tells
        the run which, rather than letting it assume the message went somewhere.
        """
        target = await self.egress.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return PrivateSend.NOT_DELIVERED
        if not target.is_group or target.surface.surface_type.is_email:
            # An email thread with other people on it is a group too, but one
            # that can be answered privately: the same thread, to the sender
            # alone, without the copies a reply-all carries.
            return _outcome(
                await self.egress.send_agent_message_for_conversation(
                    conversation_id=conversation_id,
                    message=message,
                    sender_only=target.copies_others,
                )
            )
        private = await self.egress.delivery.private_thread_for(target)
        if private is None:
            logger.info(
                "agent_surfaces.private_message.no_private_thread.observed",
                conversation_id=str(conversation_id),
                platform=target.surface.surface_type.value,
            )
            return PrivateSend.NO_PRIVATE_CHAT
        return _outcome(
            await self.egress.send_agent_message_for_conversation(
                conversation_id=private, message=message
            )
        )
