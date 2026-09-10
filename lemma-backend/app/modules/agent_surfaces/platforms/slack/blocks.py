"""Block Kit construction for the Slack surface.

Slack's ``markdown`` block (Feb 2025) renders standard Markdown natively —
headings, tables, lists, code fences, links, block quotes, task lists. That
makes it the correct container for model-authored text: the agent writes normal
Markdown and Slack renders it, instead of the agent being coached to hand-write
legacy ``mrkdwn`` and getting it wrong whenever it slipped.

The one hard constraint is a *cumulative* 12,000-character cap across every
markdown block in a single payload, which is why long answers are chunked into
separate messages rather than separate blocks in one message.
"""

from __future__ import annotations

import json
from typing import Any

# Slack caps all markdown blocks in one payload at 12,000 characters. One
# markdown block per message, with headroom for the block scaffolding itself.
MARKDOWN_BLOCK_CHAR_LIMIT = 11_800

# The notification-preview/accessibility ``text`` field. Slack truncates it
# anyway; keep it short enough to stay a preview rather than a duplicate body.
_FALLBACK_TEXT_LIMIT = 300


def markdown_block(text: str) -> dict[str, Any]:
    """One ``markdown`` block carrying model-authored Markdown verbatim."""
    return {"type": "markdown", "text": text}


def feedback_actions_block(callback_id: str) -> dict[str, Any]:
    """Thumbs up/down on an agent's answer.

    ``action_id`` carries the callback id so the ``block_actions`` parser can
    attribute the rating to the run that produced the answer.
    """
    return {
        "type": "context_actions",
        "elements": [
            {
                "type": "feedback_buttons",
                "action_id": f"{FEEDBACK_ACTION_PREFIX}{callback_id}",
                "positive_button": {
                    "text": {"type": "plain_text", "text": "Good response"},
                    "accessibility_label": "Good response",
                    "value": "good",
                },
                "negative_button": {
                    "text": {"type": "plain_text", "text": "Bad response"},
                    "accessibility_label": "Bad response",
                    "value": "bad",
                },
            }
        ],
    }


FEEDBACK_ACTION_PREFIX = "lemma_feedback:"

# Tapping this opens the "who answers here?" modal. The value carries the
# channel so the modal knows what it is configuring.
CHANNEL_SETUP_ACTION_ID = "lemma_channel_setup"


def channel_setup_confirmation_blocks(
    *, channel_name: str | None, agent_label: str
) -> list[dict[str, Any]]:
    """Confirm what was just saved.

    A modal only closes on save, so silence is indistinguishable from failure —
    which is exactly how the first working version felt.
    """
    where = f"*#{channel_name}*" if channel_name else "this channel"
    return [
        {
            "type": "markdown",
            "text": (
                f"Done — **{agent_label}** now answers in {where}. "
                "Mention Lemma here and it will reply."
            ),
        }
    ]


def channel_setup_prompt_blocks(
    *,
    channel_id: str,
    channel_name: str | None = None,
    surface_choices: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """The ephemeral nudge shown to whoever just added Lemma to a channel.

    Ephemeral on purpose: setup is a conversation with one person, and the rest
    of the channel should not have to watch it. One button rather than a form,
    because a modal is the only place two dependent selects (pod, then agent)
    can work — and opening one needs a ``trigger_id``, which only arrives on a
    real interaction.
    """
    where = f"*#{channel_name}*" if channel_name else "this channel"
    choices = list(surface_choices or [])
    if choices:
        return [
            {
                "type": "markdown",
                "text": f"I'm in {where}. Choose which pod should answer here.",
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": CHANNEL_SETUP_ACTION_ID,
                        "text": {"type": "plain_text", "text": _truncate(label, 74)},
                        "value": json.dumps(
                            {"channel_id": channel_id, "surface_id": surface_id},
                            separators=(",", ":"),
                        ),
                    }
                    for label, surface_id in choices[:5]
                ],
            },
        ]
    return [
        {
            "type": "markdown",
            "text": (
                f"I'm in {where}. Nobody answers here yet — "
                "pick which agent should, and I'll start replying when mentioned."
            ),
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": CHANNEL_SETUP_ACTION_ID,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Choose who answers"},
                    "value": channel_id,
                }
            ],
        },
    ]


# The modal's own ids. ``private_metadata`` carries the channel through the
# round trip, because a view_submission payload has no channel of its own.
CHANNEL_SETUP_VIEW_CALLBACK_ID = "lemma_channel_setup_view"
CHANNEL_SETUP_BLOCK_ID = "lemma_channel_agent"
CHANNEL_SETUP_SELECT_ACTION_ID = "lemma_channel_agent_select"


def channel_setup_modal(
    *,
    channel_id: str,
    channel_label: str | None,
    agent_name: str,
    surface_id: str | None = None,
) -> dict[str, Any]:
    """The "answer here?" confirmation.

    It used to ask *who* answers, offering every agent in the pod. One app is
    one agent now, so the only question left is whether this channel is a place
    that agent may be spoken to -- an allow-list entry rather than a choice.
    """
    where = f"#{channel_label}" if channel_label else "this channel"
    return {
        "type": "modal",
        "callback_id": CHANNEL_SETUP_VIEW_CALLBACK_ID,
        # A view_submission tells us nothing about where it came from, so the
        # channel rides along here.
        "private_metadata": json.dumps(
            {"channel_id": channel_id, "surface_id": surface_id},
            separators=(",", ":"),
        ),
        "title": {"type": "plain_text", "text": "Answer here?"},
        "submit": {"type": "plain_text", "text": "Allow"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"`{agent_name}` will reply when someone mentions it in "
                        f"*{where}*."
                    ),
                },
            },
        ],
    }


def truncate_slack_text(value: str, limit: int) -> str:
    """Trim to Slack's per-field ceiling without cutting an ellipsis in half."""
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _truncate(value: str, limit: int) -> str:
    return truncate_slack_text(value, limit)


def fallback_text(message: str) -> str:
    """A short plain-text preview for notifications and accessibility.

    Never the whole body: the markdown block is the body, and repeating it here
    makes push notifications unreadable.
    """
    collapsed = " ".join(message.split())
    if len(collapsed) <= _FALLBACK_TEXT_LIMIT:
        return collapsed
    return collapsed[: _FALLBACK_TEXT_LIMIT - 1].rstrip() + "…"


CHANNEL_ROUTE_EDIT_ACTION_ID = "lemma_channel_route_edit"
