"""Asking a WhatsApp member to write again once their 24-hour window closed.

Past 24 hours since their last message, WhatsApp takes only an approved
template from a business: anything else is accepted with a 200 and dropped.
The template carries nothing of ours. It asks them to reply, and their reply
reopens the window for the message that was really meant.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import (
    SurfaceCredentials,
    WhatsAppCredentials,
)
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.reply_targets import (
    WhatsAppReplyTarget,
    reply_target_as,
)
from app.modules.agent_surfaces.domain.surface_config import ReengageTemplate
from app.modules.agent_surfaces.platforms.send_guard import unsendable
from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppClient
from app.modules.agent_surfaces.platforms.whatsapp.payloads import whatsapp_recipient


async def send_reengage_template(
    credentials: SurfaceCredentials,
    event: ParsedInboundSurfaceEvent,
    template: ReengageTemplate,
) -> None:
    """Send ``template`` to the person ``event`` is from, or raise.

    From the number they last wrote to, as every reply here is: with a pool,
    the configured number is one they have never written to.
    """
    whatsapp = WhatsAppCredentials.of(credentials)
    client = WhatsAppClient.from_credentials(whatsapp)
    phone_number_id = (
        reply_target_as(event, WhatsAppReplyTarget).phone_number_id
        or whatsapp.phone_number_id
    )
    recipient = whatsapp_recipient(
        reply_target_as(event, WhatsAppReplyTarget), fallback_wa_id=event.sender_phone
    )
    if not phone_number_id or recipient is None or recipient.is_group:
        # A group has no reply window to reopen, and no template goes to one.
        raise unsendable(
            "WhatsApp",
            phone_number_id=phone_number_id,
            recipient=None if recipient is None or recipient.is_group else recipient,
        )
    if not client.has_credentials:
        raise unsendable("WhatsApp", access_token=None)
    await client.send_template(
        phone_number_id=phone_number_id,
        to=recipient.to,
        name=template.name,
        language=template.language,
    )


__all__ = ["send_reengage_template"]
