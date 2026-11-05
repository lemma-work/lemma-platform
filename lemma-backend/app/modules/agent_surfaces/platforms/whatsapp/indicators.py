"""Showing a WhatsApp sender their message was seen and is being worked on."""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppClient
from app.modules.agent_surfaces.platforms.whatsapp.payloads import WhatsAppRecipient

logger = get_logger(__name__)


async def acknowledge_inbound(
    client: WhatsAppClient,
    *,
    phone_number_id: str,
    recipient: WhatsAppRecipient | None,
    message_id: str,
    is_refresh: bool,
) -> None:
    """Acknowledge the inbound message with blue read ticks + a typing bubble.

    WhatsApp couples mark-as-read and the typing indicator into a single
    ``status:read`` call keyed by the inbound message id. The typing bubble
    shows for ~25s or until the next message is sent, so a single call at run
    start is enough (WhatsApp has no message-edit API for per-step progress).
    Best-effort: an indicator failure never affects the run. When the inbound
    message id is missing we fall back to the legacy 💬 reaction — but only
    on the opening call. The bubble expires after ~25s so the observer
    refreshes it on a timer, and a reaction re-posted on every tick would be
    an API call every twenty seconds to say something already said.
    """
    if recipient is not None and recipient.is_group:
        # Meta documents neither a read receipt, a typing bubble nor a
        # reaction from a business in a group -- its November 2025 guide
        # listed marking read as unsupported there. Nothing is sent rather
        # than a call a group may refuse on every turn.
        return
    sender_wa_id = recipient.to if recipient is not None else None

    if message_id:
        try:
            await client.mark_read_and_typing(
                phone_number_id=phone_number_id,
                message_id=message_id,
            )
            return
        except Exception:
            # Best-effort indicator, and the reaction fallback below still
            # runs, so this is `info` rather than `warning`. It said "log at
            # debug so it is diagnosable without spamming warnings" -- and
            # the deployment runs `LOG_LEVEL=INFO`, where `debug` is not
            # diagnosable, it is absent. The exception goes with it.
            logger.info(
                "agent_surfaces.service.whatsapp_mark_read_typing_best.observed",
                exc_info=True,
            )

    # Fallback: no inbound id (or read/typing rejected) — post a reaction so
    # the user still sees the agent acknowledged the message.
    if is_refresh or not sender_wa_id or not message_id:
        return
    try:
        await client.react(
            phone_number_id=phone_number_id,
            to=sender_wa_id,
            message_id=message_id,
            emoji="\U0001f4ac",
        )
    except Exception:
        # The last rung of the acknowledgement ladder: read receipt, then
        # typing, then this. Nothing follows it, so the person sees no
        # acknowledgement at all -- worth a record, with the reason.
        logger.info(
            "agent_surfaces.service.whatsapp_reaction_indicator_best_effort.observed",
            exc_info=True,
        )


__all__ = ["acknowledge_inbound"]
