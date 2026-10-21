"""WhatsApp approval and question cards, remembered and later retired.

WhatsApp cannot edit a sent message, so a card's buttons cannot be taken away.
What it can do is reply to the card: a short line quoting it, saying it is no
longer needed, so the person scrolling back to it sees the answer right under
it before tapping. A tap is still answered by the interaction path either way.

Only for a card a new message cancelled. A card answered by a typed reply has
that reply right under it already, and a line saying "answered" would repeat it.
One answered in the web app gets none either: it may be answered long after
the 24-hour window a free-form line needs, and a tap on it is still turned
away by the interaction path.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.platforms.decision_cards import remember_decision_card
from app.modules.agent_surfaces.platforms.whatsapp.client import WhatsAppClient


async def remember_whatsapp_card(
    callback_id: str,
    message_id: str | None,
    *,
    phone_number_id: str,
    recipient_wa_id: str,
) -> None:
    """Remember a posted card by its message id, and the number it went from."""
    if not message_id:
        return
    await remember_decision_card(
        callback_id,
        DecisionCardHandle(
            platform="WHATSAPP",
            channel=recipient_wa_id,
            message_id=message_id,
            extra={"phone_number_id": phone_number_id},
        ),
    )


async def retire_whatsapp_card(
    client: WhatsAppClient, handle: DecisionCardHandle, reason: CardRetirement
) -> None:
    """Reply to a cancelled card, quoting it, that it is no longer needed."""
    phone_number_id = handle.extra.get("phone_number_id")
    if reason is not CardRetirement.SUPERSEDED or not phone_number_id:
        return
    await client.send_text(
        phone_number_id=phone_number_id,
        to=handle.channel,
        body=f"{reason.note} You moved on before answering, so it was cancelled.",
        reply_to_message_id=handle.message_id,
    )


__all__ = ["remember_whatsapp_card", "retire_whatsapp_card"]
