"""Telling a member something: a line in their own channel.

What a member, the API or the pod has to tell a member is a line in that
member's DIRECT channel. It is there whatever happens next. Posting it plans
its push like any line's (``channels``); carrying it to them on a platform is
the delivery executor's (``channel_deliveries``), so this sends nothing itself.

Membership fails closed: a recipient who is not in the pod is refused outright,
so no wiring bug becomes "any user id can be messaged".

A notice -- a contact who could not be answered, a bot whose keeper left -- is
a note to the member alone (``members_only``): in their channel, never pushed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
from app.modules.channels.contracts import (
    ChannelMessage,
    NewLine,
    Principal,
    Sender,
    direct_channel,
    post,
)


@dataclass(frozen=True, slots=True)
class DirectMessage:
    pod_id: UUID
    recipient_user_id: UUID
    text: str
    #: The member it comes from; ``None`` for the pod itself.
    actor_user_id: UUID | None = None
    actor_agent_id: UUID | None = None
    agent_name: str | None = None
    #: Makes a retried send the first one, not a second line.
    key: str | None = None


class DirectMessages:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_membership_port: SurfacePodMembershipPort,
    ) -> None:
        self.uow = uow
        self.membership = pod_membership_port

    async def send(self, message: DirectMessage) -> ChannelMessage:
        """Post the line in their channel; its push follows on its own."""
        pod_id, recipient = message.pod_id, message.recipient_user_id
        if await self.membership.get_pod_member_id(recipient, pod_id) is None:
            raise AgentSurfaceValidationError(
                "The recipient is not a member of this pod."
            )
        channel = await direct_channel(self.uow, pod_id=pod_id, user_id=recipient)
        # Said by the pod's agent for whoever it comes from: nobody else is
        # ever in a member's own channel.
        sender = Sender.agent(
            effective_agent_id(message.actor_agent_id, pod_id=pod_id),
            on_behalf_of=Principal.member(message.actor_user_id or recipient),
            display_name=message.agent_name,
        )
        line = await post(
            self.uow,
            channel_id=channel.id,
            line=NewLine(
                sender=sender,
                text=message.text,
                client_message_id=_line_name(message.key),
            ),
        )
        if line is None:
            raise AgentSurfaceValidationError("There is nothing to send.")
        if (
            message.key is not None
            and line.edited_at is None
            and not line.is_deleted
            and line.text != message.text.replace("\x00", "")
        ):
            # The key named an earlier send that said something else: a
            # retry is the same request, never a quiet stand-in for a new one.
            raise AgentSurfaceValidationError(
                "That Idempotency-Key was used for another message; send a new key."
            )
        return line


def _line_name(key: str | None) -> str | None:
    """The line's ``client_message_id`` for a send's key, inside its 64."""
    if key is None:
        return None
    if len(key) > 61:
        key = hashlib.sha256(key.encode()).hexdigest()[:48]
    return f"dm:{key}"


async def tell_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    text: str,
    urgent: bool = False,
    key: str | None = None,
) -> ChannelMessage | None:
    """A notice to one member: a note in their own channel. Not pushed, unless
    ``urgent`` -- somebody is waiting on them -- when it names them, so it
    reaches them outside the app whatever their push budget. ``key`` makes it
    said once: the same key again is the first note, not a second."""
    channel = await direct_channel(uow, pod_id=pod_id, user_id=user_id)
    agent_id = effective_agent_id(None, pod_id=pod_id)
    return await post(
        uow,
        channel_id=channel.id,
        line=NewLine(
            sender=Sender.agent(agent_id, on_behalf_of=Principal.member(user_id)),
            text=text,
            members_only=True,
            client_message_id=_line_name(key),
        ),
        mentions=[Principal.member(user_id)] if urgent else (),
    )
