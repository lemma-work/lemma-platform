"""Whether a contact's rules refuse the agent's answer before it is written.

An answer to a contact goes out as a delivery, which holds it to their rules
(``channel_deliveries``). What the run shows while it works -- a live stream,
a progress message, a typing bubble -- reaches them before any delivery
exists, so it is held to the same rules when the run starts: somebody who
sent STOP, or whose WhatsApp window has closed, is shown nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.services.contact_reach import reach_for
from app.modules.channels.contracts import ChannelKind, channel_by_id
from app.modules.channels.contracts.places import reply_address
from app.modules.contacts.contracts.outreach import OutreachKind, may_send

#: Whether a conversation's contact refuses its answer now.
type ContactGate = Callable[[UUID], Awaitable[bool]]


async def contact_refuses_answers(
    uow_factory: UnitOfWorkFactory, conversation_id: UUID
) -> bool:
    """True when the conversation answers a contact at a place their rules
    do not let the agent write to now."""
    async with uow_factory() as uow:
        address = await reply_address(uow, conversation_id)
        if address is None:
            return False
        source = address.source
        channel = await channel_by_id(uow, source.channel_id)
        if (
            channel is None
            or channel.kind is not ChannelKind.CONTACT
            or channel.contact_id is None
        ):
            return False
        reach = reach_for(
            source.platform,
            address=source.external_user_id,
            last_inbound_at=source.last_inbound_at,
        )
        if reach is None:
            return False
        to, window = reach
        verdict = await may_send(
            uow,
            pod_id=channel.pod_id,
            contact_id=channel.contact_id,
            to=to,
            kind=OutreachKind.AGENT_ANSWER,
            at=datetime.now(timezone.utc),
            window=window,
        )
    return not verdict.allowed


__all__ = ["ContactGate", "contact_refuses_answers"]
