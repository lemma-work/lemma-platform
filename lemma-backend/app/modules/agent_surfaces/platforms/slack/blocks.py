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
    *, channel_id: str, channel_name: str | None = None
) -> list[dict[str, Any]]:
    """The ephemeral nudge shown to whoever just added Lemma to a channel.

    Ephemeral on purpose: setup is a conversation with one person, and the rest
    of the channel should not have to watch it. One button rather than a form,
    because a modal is the only place two dependent selects (pod, then agent)
    can work — and opening one needs a ``trigger_id``, which only arrives on a
    real interaction.
    """
    where = f"*#{channel_name}*" if channel_name else "this channel"
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

# Value used for "the pod's own assistant" — the surface default, which is
# stored as an empty agent_name on the route rather than as a named agent.
POD_ASSISTANT_VALUE = "__pod_assistant__"


def channel_setup_modal(
    *,
    channel_id: str,
    channel_label: str | None,
    agent_names: list[str],
) -> dict[str, Any]:
    """The "who answers here?" modal.

    Two dependent choices cannot live in a message — Slack messages can't
    cascade one select off another — which is the whole reason this is a modal
    and the reason the ephemeral carries a button rather than a form.

    The pod assistant is offered first because it is the answer for someone who
    has not built a named agent yet, and it is what an empty route already means.
    """
    where = f"#{channel_label}" if channel_label else "this channel"
    options = [
        {
            "text": {"type": "plain_text", "text": "Pod assistant"},
            "value": POD_ASSISTANT_VALUE,
        }
    ]
    options.extend(
        {
            "text": {"type": "plain_text", "text": _truncate(name, 74)},
            "value": name,
        }
        for name in agent_names[:99]
    )
    return {
        "type": "modal",
        "callback_id": CHANNEL_SETUP_VIEW_CALLBACK_ID,
        # A view_submission tells us nothing about where it came from, so the
        # channel rides along here.
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": "Who answers here?"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Pick who replies when someone mentions Lemma in *{where}*.",
                },
            },
            {
                "type": "input",
                "block_id": CHANNEL_SETUP_BLOCK_ID,
                "label": {"type": "plain_text", "text": "Answered by"},
                "element": {
                    "type": "static_select",
                    "action_id": CHANNEL_SETUP_SELECT_ACTION_ID,
                    "placeholder": {"type": "plain_text", "text": "Choose an agent"},
                    "options": options,
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


DM_AGENT_SETUP_ACTION_ID = "lemma_dm_agent_setup"
DM_AGENT_VIEW_CALLBACK_ID = "lemma_dm_agent_view"
DM_AGENT_BLOCK_ID = "lemma_dm_agent"
DM_AGENT_SELECT_ACTION_ID = "lemma_dm_agent_select"
CHANNEL_ROUTE_EDIT_ACTION_ID = "lemma_channel_route_edit"


def dm_agent_modal(*, agent_names: list[str], current: str | None) -> dict[str, Any]:
    """Pick who answers *your* DMs.

    Per person, not per workspace: two people in the same Slack can talk to
    different agents, which is the limit Slack used to impose and no longer has
    to.
    """
    options = [
        {
            "text": {"type": "plain_text", "text": "Pod assistant"},
            "value": POD_ASSISTANT_VALUE,
        }
    ]
    options.extend(
        {"text": {"type": "plain_text", "text": _truncate(name, 74)}, "value": name}
        for name in agent_names[:99]
    )
    element: dict[str, Any] = {
        "type": "static_select",
        "action_id": DM_AGENT_SELECT_ACTION_ID,
        "placeholder": {"type": "plain_text", "text": "Choose an agent"},
        "options": options,
    }
    selected = next((o for o in options if o["value"] == (current or "")), None)
    if selected is not None:
        element["initial_option"] = selected
    return {
        "type": "modal",
        "callback_id": DM_AGENT_VIEW_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Who answers you?"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "This only changes *your* direct messages. Everyone else keeps theirs.",
                },
            },
            {
                "type": "input",
                "block_id": DM_AGENT_BLOCK_ID,
                "label": {"type": "plain_text", "text": "Answered by"},
                "element": element,
            },
        ],
    }


AGENT_DM_ACTION_ID = "lemma_agent_dm"


def app_home_view(
    *,
    pod_name: str | None,
    dm_agent_name: str | None,
    channel_routes: list,
    agents: list | None = None,
    apps: list | None = None,
    workspace_url: str | None = None,
    logo_url: str | None = None,
) -> dict[str, Any]:
    """The App Home — the one screen that has to explain and sell Lemma.

    Ordered by what a first-time viewer needs: what this is, one thing to try,
    then what exists, and only then how it is wired up. Configuration is real
    but it is not the pitch, so it sits at the bottom.

    Slack gives no CSS and no layout control, so the craft here is entirely in
    ordering, copy, and using ``card`` blocks (Apr 2026) instead of stacked
    sections — cards are the only native thing that reads as an object rather
    than as a paragraph.
    """
    agents = list(agents or [])
    apps = list(apps or [])
    blocks: list[dict[str, Any]] = []

    # Masthead. The logo is skipped unless it is publicly fetchable: Slack
    # loads it from its own servers, so a localhost URL renders an empty box.
    if logo_url and str(logo_url).startswith("https://"):
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "image", "image_url": logo_url, "alt_text": "Lemma"},
                    {"type": "mrkdwn", "text": "*Lemma*"},
                ],
            }
        )
    blocks.append(
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{pod_name}" if pod_name else "Your agents, in Slack",
            },
        }
    )
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Ask a question in plain English and get an answer from your "
                    "own data — tables, files, workflows and connected tools. "
                    "No dashboards, no context switch."
                ),
            },
        }
    )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "💬 Message me here  ·  # @-mention me in any channel",
                }
            ],
        }
    )

    # One thing to try, before any configuration. A new person should be able
    # to get a real answer without reading anything else on this page.
    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Try one*"},
        }
    )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": f"{AGENT_DM_ACTION_ID}:what_can_you_do",
                    "text": {"type": "plain_text", "text": "What can you do?"},
                    "value": "What can you help me with in this workspace?",
                },
                {
                    "type": "button",
                    "action_id": f"{AGENT_DM_ACTION_ID}:show_data",
                    "text": {"type": "plain_text", "text": "Show me my data"},
                    "value": "What tables and records can you see?",
                },
                {
                    "type": "button",
                    "action_id": f"{AGENT_DM_ACTION_ID}:whats_new",
                    "text": {"type": "plain_text", "text": "Catch me up"},
                    "value": "Summarise what changed in this workspace recently.",
                },
            ],
        }
    )

    if agents:
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Agents*"}}
        )
        for name, description in agents[:8]:
            summary = _truncate(str(description or "").strip(), 160)
            blocks.append(
                {
                    "type": "card",
                    "title": {"type": "plain_text", "text": _truncate(str(name), 74)},
                    "body": {
                        "type": "mrkdwn",
                        "text": summary or "_No description yet._",
                    },
                }
            )

    if apps:
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Apps*"}}
        )
        for name, url in apps[:6]:
            blocks.append(
                {
                    "type": "card",
                    "title": {"type": "plain_text", "text": _truncate(str(name), 74)},
                    "body": {"type": "mrkdwn", "text": "Opens in your browser."},
                    "actions": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Open"},
                            "url": url,
                            "style": "primary",
                        }
                    ],
                }
            )

    # Settings last: real, but not the pitch.
    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Your direct messages*\nAnswered by `{dm_agent_name}`"
                    if dm_agent_name
                    else "*Your direct messages*\nAnswered by the pod assistant"
                ),
            },
            "accessory": {
                "type": "button",
                "action_id": DM_AGENT_SETUP_ACTION_ID,
                "text": {"type": "plain_text", "text": "Change"},
            },
        }
    )
    if channel_routes:
        lines = "\n".join(
            f"<#{channel_id}> \u2192 `{agent or 'Pod assistant'}`"
            for channel_id, agent in list(channel_routes)[:20]
        )
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Channels*\n{lines}"}}
        )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Channels*\nInvite me to a channel and I'll ask who "
                        "should answer there."
                    ),
                },
            }
        )

    footer: list[dict[str, Any]] = []
    if workspace_url:
        footer.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open Lemma"},
                "url": workspace_url,
            }
        )
    if footer:
        blocks.append({"type": "divider"})
        blocks.append({"type": "actions", "elements": footer})
    return {"type": "home", "blocks": blocks}
