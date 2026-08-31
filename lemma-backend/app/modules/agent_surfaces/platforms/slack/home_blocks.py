"""The Slack App Home — the one screen that has to explain and sell Lemma.

Split from :mod:`blocks`, which builds what Lemma *says*: messages, modals and
in-channel prompts. This builds the place a person goes to *configure* it, and
it is the half that grows — every new thing a pod holds wants a row here.
Sharing a file with message rendering meant one screen's copy edits kept landing
next to streaming's markdown limits.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.platforms.slack.blocks import (
    DEFAULT_RESPONDER_NAME,
    _truncate,
)


AGENT_DM_ACTION_ID = "lemma_agent_dm"
SURFACE_SELECT_ACTION_ID = "lemma_surface_select"


def _home(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "home", "blocks": blocks}


def _section_break(title: str) -> list[dict[str, Any]]:
    """A divider and a bold label: how every section on this page opens."""
    return [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}},
    ]


def _notice_blocks(heading: str, message: str) -> list[dict[str, Any]]:
    """A heading and one line, for when there is nothing else to show."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": heading}},
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
    ]


def _pod_choice_blocks(choices: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Which pod this workspace should show, when it is connected to several."""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Choose a Lemma pod"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "This Slack workspace is connected to more than one pod. Pick the one this app should show you.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": SURFACE_SELECT_ACTION_ID,
                    "text": {"type": "plain_text", "text": _truncate(label, 74)},
                    "value": surface_id,
                }
                for label, surface_id in choices[:5]
            ],
        },
    ]


def _masthead_blocks(
    *, pod_name: str | None, logo_url: str | None
) -> list[dict[str, Any]]:
    """What this is, before anything asks the reader to configure it."""
    blocks: list[dict[str, Any]] = []
    # The logo is skipped unless it is publicly fetchable: Slack loads it from
    # its own servers, so a localhost URL renders an empty box.
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
    return blocks


def _try_one_blocks() -> list[dict[str, Any]]:
    """One thing to try, before any configuration.

    A new person should be able to get a real answer without reading anything
    else on this page.
    """
    return [
        *_section_break("Try one"),
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
        },
    ]


def _agent_blocks(agents: list) -> list[dict[str, Any]]:
    """One card per agent, capped -- the Home is a summary, not a directory."""
    if not agents:
        return []
    return _section_break("Agents") + [
        {
            "type": "card",
            "title": {"type": "plain_text", "text": _truncate(str(name), 74)},
            "body": {
                "type": "mrkdwn",
                "text": _truncate(str(description or "").strip(), 160)
                or "_No description yet._",
            },
        }
        for name, description in agents[:8]
    ]


def _app_blocks(apps: list) -> list[dict[str, Any]]:
    """One card per app, each a way out to the browser."""
    if not apps:
        return []
    return _section_break("Apps") + [
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
        for name, url in apps[:6]
    ]


def _channel_routes_block(channel_routes: list) -> dict[str, Any]:
    """Who answers in which channel, or how to get the first one wired up."""
    if not channel_routes:
        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Channels*\nInvite me to a channel and I'll ask who "
                    "should answer there."
                ),
            },
        }
    routes = "\n".join(
        f"<#{channel_id}> \u2192 `{agent or DEFAULT_RESPONDER_NAME}`"
        for channel_id, agent in list(channel_routes)[:20]
    )
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Channels*\n{routes}"},
    }


def _settings_blocks(
    channel_routes: list,
    agent_name: str,
) -> list[dict[str, Any]]:
    """Settings last: real, but not the pitch.

    A row that states who this bot is, and carries no button. It used to offer
    "Change", because one app could answer as any of the pod's agents and each
    person picked their own. One app is one agent now, so there is nothing to
    change -- and stating the fact reads better than an affordance that would
    only ever refuse.
    """
    return [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Your direct messages*\nYou are talking to `{agent_name}`",
            },
        },
        _channel_routes_block(channel_routes),
    ]


def _footer_blocks(workspace_url: str | None) -> list[dict[str, Any]]:
    """The way out to Lemma itself, when there is somewhere to go."""
    if not workspace_url:
        return []
    return [
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Lemma"},
                    "url": workspace_url,
                }
            ],
        },
    ]


def app_home_view(
    *,
    pod_name: str | None,
    agent_name: str,
    channel_routes: list,
    agents: list | None = None,
    apps: list | None = None,
    workspace_url: str | None = None,
    logo_url: str | None = None,
    surface_choices: list[tuple[str, str]] | None = None,
    access_message: str | None = None,
) -> dict[str, Any]:
    """The App Home — the one screen that has to explain and sell Lemma.

    Ordered by what a first-time viewer needs: what this is, one thing to try,
    then what exists, and only then how it is wired up. Configuration is real
    but it is not the pitch, so it sits at the bottom.

    Slack gives no CSS and no layout control, so the craft here is entirely in
    ordering, copy, and using ``card`` blocks (Apr 2026) instead of stacked
    sections — cards are the only native thing that reads as an object rather
    than as a paragraph. Each section below builds its own blocks, so that
    order is the only thing this function states.
    """
    if access_message:
        return _home(_notice_blocks("Lemma", access_message))
    choices = list(surface_choices or [])
    if choices:
        return _home(_pod_choice_blocks(choices))
    return _home(
        [
            *_masthead_blocks(pod_name=pod_name, logo_url=logo_url),
            *_try_one_blocks(),
            *_agent_blocks(list(agents or [])),
            *_app_blocks(list(apps or [])),
            *_settings_blocks(channel_routes, agent_name),
            *_footer_blocks(workspace_url),
        ]
    )
