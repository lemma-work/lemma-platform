"""Slack approval and question cards, remembered and later retired.

Slack rewrites a message whole with ``chat.update``: there is no call that
takes the buttons off and leaves the rest. So the card is remembered with its
own blocks minus every control, and retiring it writes those back with one
line saying why the buttons went.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypedDict


from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.platforms.decision_cards import remember_decision_card

if TYPE_CHECKING:
    from slack_sdk.web.async_client import AsyncWebClient

#: Block types a person acts on. A retired card keeps everything else.
_CONTROL_BLOCK_TYPES = frozenset({"actions", "input"})


def without_controls(blocks: object) -> list[dict[str, object]]:
    """The blocks of a card a person can only read."""
    if not isinstance(blocks, list):
        return []
    return [
        block
        for block in blocks
        if isinstance(block, dict) and block.get("type") not in _CONTROL_BLOCK_TYPES
    ]


class RetiredCard(TypedDict):
    """The ``chat.update`` arguments that rewrite a card."""

    channel: str
    ts: str
    text: str
    blocks: list[dict[str, object]]


def retired_card(handle: DecisionCardHandle, reason: CardRetirement) -> RetiredCard:
    """The ``chat.update`` arguments that rewrite the card without controls."""
    note: dict[str, object] = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"_{reason.note}_"}],
    }
    return RetiredCard(
        channel=handle.channel,
        ts=handle.message_id,
        # A notification preview and a client without blocks show this.
        text=reason.note,
        blocks=[*handle.blocks, note],
    )


async def remember_slack_card(
    callback_id: str, ts: str | None, payload: Mapping[str, object]
) -> None:
    """Remember a posted card by its ``ts``. Nothing to keep for an ephemeral."""
    channel = str(payload.get("channel") or "")
    if not ts or not channel:
        return
    await remember_decision_card(
        callback_id,
        DecisionCardHandle(
            platform="SLACK",
            channel=channel,
            message_id=ts,
            blocks=without_controls(payload.get("blocks")),
            text=str(payload.get("text") or ""),
        ),
    )


async def retire_slack_card(
    client: AsyncWebClient, handle: DecisionCardHandle, reason: CardRetirement
) -> None:
    await client.chat_update(**retired_card(handle, reason))


__all__ = [
    "remember_slack_card",
    "retire_slack_card",
    "retired_card",
    "without_controls",
]
