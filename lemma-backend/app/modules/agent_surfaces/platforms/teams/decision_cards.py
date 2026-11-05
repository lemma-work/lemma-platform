"""Teams approval and question cards, remembered and later retired.

An Adaptive Card's actions stay tappable for as long as the message exists, and
Bot Framework edits an activity only whole (``UpdateActivity``, a PUT on it).
So a card is remembered with its readable body, minus the inputs, and with where
the activity lives -- the service URL and conversation the bot posted it to, and
the tenant whose token reaches it. Retiring it puts back that body with a note
and no actions.
"""

from __future__ import annotations

from urllib.parse import quote

from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.reply_targets import (
    TeamsReplyTarget,
    reply_target_as,
)
from app.modules.agent_surfaces.platforms.decision_cards import remember_decision_card
from app.modules.agent_surfaces.platforms.teams import client

_ADAPTIVE_CARD = "application/vnd.microsoft.card.adaptive"


def _readable_body(card: dict[str, object]) -> list[dict[str, object]]:
    """The card's body without its inputs: what a person can only read."""
    body = card.get("body")
    if not isinstance(body, list):
        return []
    return [
        element
        for element in body
        if isinstance(element, dict)
        and not str(element.get("type") or "").startswith("Input.")
    ]


async def remember_teams_card(
    callback_id: str,
    activity_id: str | None,
    event: ParsedInboundSurfaceEvent,
    card: dict[str, object],
) -> None:
    """Remember a posted card by its activity id."""
    target = reply_target_as(event, TeamsReplyTarget)
    conversation_id = target.conversation_id or ""
    if not activity_id or not conversation_id or not event.tenant_id:
        return
    await remember_decision_card(
        callback_id,
        DecisionCardHandle(
            platform="TEAMS",
            channel=conversation_id,
            message_id=activity_id,
            blocks=_readable_body(card),
            extra={
                "service_url": target.service_url or "",
                "conversation_id": conversation_id,
                "tenant_id": event.tenant_id,
            },
        ),
    )


def activity_url(handle: DecisionCardHandle) -> str:
    """Where the card's activity is edited, or raise.

    Built on `client.bf_service_url`, so a remembered service URL is held to
    the same rule as one read off an inbound activity: the bot token goes to a
    Bot Framework host or nowhere.
    """
    base = client.bf_service_url(handle.extra.get("service_url"))
    return (
        f"{base}/v3/conversations/{quote(handle.channel)}"
        f"/activities/{quote(handle.message_id)}"
    )


def retired_activity(
    handle: DecisionCardHandle, reason: CardRetirement
) -> dict[str, object]:
    """The activity that replaces the card: its body, a note, no actions."""
    note: dict[str, object] = {
        "type": "TextBlock",
        "text": reason.note,
        "isSubtle": True,
        "wrap": True,
    }
    return {
        "type": "message",
        "id": handle.message_id,
        "attachments": [
            {
                "contentType": _ADAPTIVE_CARD,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [*handle.blocks, note],
                },
            }
        ],
    }


__all__ = ["activity_url", "remember_teams_card", "retired_activity"]
