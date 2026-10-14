"""What Lemma's own Slack app shows: it manages teammates, it does not answer.

Every teammate answers in Slack through an app of its own. Lemma's app is where
a workspace sees them, adds one, and hands a channel to one -- so its Home tab
is a roster with an "Add to Slack" on each teammate not there yet, its modal
makes the teammate's app, and its only message in a channel is a private "who
should join?" to the person who invited it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

ACTION_PREFIX = "lemma_mgr_"
#: Opens the "Add to Slack" modal for one teammate. Value: the pod id.
ADD_ACTION_ID = "lemma_mgr_add"
#: Brings a teammate into the channel the manager was invited to.
#: Value: ``{"channel": …, "surface": …}``.
JOIN_ACTION_ID = "lemma_mgr_join"
#: URL buttons. Slack still reports the tap; nothing is done with it.
LINK_ACTION_ID = "lemma_mgr_link"
ADD_VIEW_CALLBACK_ID = "lemma_mgr_add_view"

#: Teammates a Home lists before pointing at Lemma for the rest; a Home holds
#: 100 blocks and each teammate is one.
ROSTER_SHOWN = 40
#: Teammates a channel hand-off offers: four rows of Slack's 25 per actions block.
_PICKER_SHOWN = 100
_BUTTONS_PER_ROW = 25

TOKEN_BLOCK_ID = "lemma_mgr_token"
CLIENT_ID_BLOCK_ID = "lemma_mgr_client_id"
CLIENT_SECRET_BLOCK_ID = "lemma_mgr_client_secret"
SIGNING_SECRET_BLOCK_ID = "lemma_mgr_signing_secret"
INPUT_ACTION_ID = "value"

SLACK_APPS_URL = "https://api.slack.com/apps"
_TITLE = "Add to Slack"


@dataclass(frozen=True)
class RosterEntry:
    """One teammate on the manager's Home tab."""

    pod_id: str
    name: str
    #: The teammate's bot user, when it is already in this workspace.
    bot_user_id: str | None = None
    #: Where "Message" opens: the teammate's app in Slack.
    open_url: str | None = None


def _text(text: str) -> dict[str, object]:
    return {"type": "mrkdwn", "text": text}


def _plain(text: str) -> dict[str, object]:
    return {"type": "plain_text", "text": text, "emoji": True}


def _url_button(label: str, url: str, *, primary: bool = False) -> dict[str, object]:
    button: dict[str, object] = {
        "type": "button",
        "text": _plain(label),
        "url": url,
        "action_id": LINK_ACTION_ID,
    }
    if primary:
        button["style"] = "primary"
    return button


def _home(blocks: list[dict[str, object]]) -> dict[str, object]:
    return {"type": "home", "blocks": blocks}


def connect_home(*, link_url: str, team_name: str | None) -> dict[str, object]:
    """For someone Lemma does not know yet: one way forward, signing in once."""
    where = f" in {team_name}" if team_name else ""
    return _home(
        [
            {"type": "header", "text": _plain("Lemma")},
            {
                "type": "section",
                "text": _text(
                    f"Lemma's teammates answer{where} under their own names. "
                    "Connect your Lemma account to see them and add one."
                ),
            },
            {
                "type": "actions",
                "elements": [_url_button("Connect", link_url, primary=True)],
            },
            {
                "type": "context",
                "elements": [
                    _text("You sign in to Lemma once. After that I know it's you.")
                ],
            },
        ]
    )


def outsider_home(*, organization_name: str, link_url: str) -> dict[str, object]:
    """Known to Lemma, but not in the organization this workspace belongs to."""
    return _home(
        [
            {"type": "header", "text": _plain("Lemma")},
            {
                "type": "section",
                "text": _text(
                    f"This workspace belongs to *{organization_name}* on Lemma, and "
                    "your Lemma account isn't in it. Ask an owner to invite you."
                ),
            },
            {
                "type": "actions",
                "elements": [_url_button("Use another account", link_url)],
            },
        ]
    )


def roster_home(
    *,
    team_name: str | None,
    teammates: Sequence[RosterEntry],
    lemma_url: str,
    more: int = 0,
) -> dict[str, object]:
    """The roster: who answers here, and an "Add to Slack" for who does not.

    ``teammates`` is at most :data:`ROSTER_SHOWN`; ``more`` is how many the
    caller left off, so the Home can say so rather than quietly end.
    """
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": _plain(f"Teammates in {team_name}" if team_name else "Teammates"),
        },
        {
            "type": "section",
            "text": _text(
                "Each teammate answers in Slack under its own name. Invite me to a "
                "channel and I'll bring in the one you pick."
            ),
        },
        {"type": "divider"},
    ]
    if not teammates:
        blocks.append(
            {
                "type": "section",
                "text": _text("You're not on any teammate yet. Hire one in Lemma."),
            }
        )
    for teammate in teammates[:ROSTER_SHOWN]:
        if teammate.bot_user_id:
            status = f"In Slack as <@{teammate.bot_user_id}>"
            accessory = (
                _url_button("Message", teammate.open_url) if teammate.open_url else None
            )
        else:
            status = "Not in Slack yet"
            accessory = {
                "type": "button",
                "text": _plain("Add to Slack"),
                "action_id": ADD_ACTION_ID,
                "value": teammate.pod_id,
            }
        section: dict[str, object] = {
            "type": "section",
            "text": _text(f"*{teammate.name}*\n{status}"),
        }
        if accessory:
            section["accessory"] = accessory
        blocks.append(section)
    if more > 0:
        blocks.append(
            {
                "type": "context",
                "elements": [_text(f"{more} more teammates are in Lemma.")],
            }
        )
    blocks += [
        {"type": "divider"},
        {"type": "actions", "elements": [_url_button("Open Lemma", lemma_url)]},
    ]
    return _home(blocks)


def _modal(
    *, blocks: list[dict[str, object]], metadata: dict[str, str], submit: str | None
) -> dict[str, object]:
    view: dict[str, object] = {
        "type": "modal",
        "callback_id": ADD_VIEW_CALLBACK_ID,
        "private_metadata": json.dumps(metadata),
        "title": _plain(_TITLE),
        "close": _plain("Close"),
        "blocks": blocks,
    }
    if submit:
        view["submit"] = _plain(submit)
    return view


def add_modal_with_lemma(*, name: str, metadata: dict[str, str]) -> dict[str, object]:
    """The organization has let Lemma make apps here: one button."""
    return _modal(
        metadata=metadata,
        submit="Create the app",
        blocks=[
            {
                "type": "section",
                "text": _text(
                    f"Lemma makes *{name}*'s app and names it {name}. Then Slack "
                    "asks you to allow it, and that's all."
                ),
            }
        ],
    )


def _optional_input(
    block_id: str, label: str, placeholder: str = ""
) -> dict[str, object]:
    element: dict[str, object] = {
        "type": "plain_text_input",
        "action_id": INPUT_ACTION_ID,
    }
    if placeholder:
        element["placeholder"] = _plain(placeholder)
    return {
        "type": "input",
        "block_id": block_id,
        "optional": True,
        "label": _plain(label),
        "element": element,
    }


def add_modal_by_hand(
    *, name: str, metadata: dict[str, str], manifest_url: str
) -> dict[str, object]:
    """No token yet: let Lemma make apps from now on, or make this one yourself."""
    return _modal(
        metadata=metadata,
        submit="Continue",
        blocks=[
            {
                "type": "section",
                "text": _text(
                    "*Let Lemma make the app.* On Slack's app page, under _Your App "
                    "Configuration Tokens_, choose _Generate Token_ and paste the "
                    "refresh token here. You do this once per workspace."
                ),
                "accessory": _url_button("Open Slack apps", SLACK_APPS_URL),
            },
            _optional_input(TOKEN_BLOCK_ID, "Refresh token", "xoxe-1-…"),
            {"type": "divider"},
            {
                "type": "section",
                "text": _text(
                    f"*Or make it yourself.* Create {name}'s app from Lemma's "
                    "manifest, install it, then paste three values from its _Basic "
                    "Information_ page."
                ),
                "accessory": _url_button("Create the app", manifest_url),
            },
            _optional_input(CLIENT_ID_BLOCK_ID, "Client ID"),
            _optional_input(CLIENT_SECRET_BLOCK_ID, "Client secret"),
            _optional_input(SIGNING_SECRET_BLOCK_ID, "Signing secret"),
        ],
    )


def working_modal(*, name: str, metadata: dict[str, str]) -> dict[str, object]:
    return _modal(
        metadata=metadata,
        submit=None,
        blocks=[{"type": "section", "text": _text(f"Making {name}'s app…")}],
    )


def allow_modal(
    *, name: str, metadata: dict[str, str], authorize_url: str, app_id: str
) -> dict[str, object]:
    return _modal(
        metadata=metadata,
        submit=None,
        blocks=[
            {
                "type": "section",
                "text": _text(
                    f"*{name}'s app is ready.* Allow it in Slack and {name} joins "
                    "this workspace. I'll tell you here when it's in."
                ),
            },
            {
                "type": "actions",
                "elements": [_url_button(f"Allow {name}", authorize_url, primary=True)],
            },
            {
                "type": "context",
                "elements": [
                    _text(
                        "Slack takes the bot's picture only on its own page: "
                        f"<{_app_page(app_id)}|add {name}'s picture>."
                    )
                ],
            },
        ],
    )


def _app_page(app_id: str) -> str:
    """The app's own settings page; the app list when Lemma doesn't know its id."""
    return f"{SLACK_APPS_URL}/{app_id}/general" if app_id else SLACK_APPS_URL


def problem_modal(*, message: str, metadata: dict[str, str]) -> dict[str, object]:
    return _modal(
        metadata=metadata,
        submit=None,
        blocks=[{"type": "section", "text": _text(message)}],
    )


def channel_picker(
    *, channel_id: str, teammates: Sequence[tuple[str, str]]
) -> tuple[str, list[dict[str, object]]]:
    """Asked privately of whoever invited Lemma: which teammate joins.

    ``teammates`` are ``(surface_id, name)``, only the ones already in this
    workspace -- bringing in one that is not would need its Allow first.
    """
    if not teammates:
        text = (
            f"No teammate is in Slack yet, so there's nobody to bring into "
            f"<#{channel_id}>. Add one from my Home tab, then invite me again."
        )
        return text, [{"type": "section", "text": _text(text)}]
    text = f"Who should join <#{channel_id}>?"
    buttons = [
        {
            "type": "button",
            "text": _plain(name[:75]),
            # Unique per button, as Slack requires within one message.
            "action_id": f"{JOIN_ACTION_ID}:{index}",
            "value": json.dumps({"channel": channel_id, "surface": surface_id}),
        }
        for index, (surface_id, name) in enumerate(teammates[:_PICKER_SHOWN])
    ]
    blocks: list[dict[str, object]] = [
        {"type": "section", "text": _text(f"{text} Pick one and I'll step out.")}
    ]
    # An actions block holds 25 elements, so the buttons go in rows of it.
    blocks += [
        {"type": "actions", "elements": buttons[start : start + _BUTTONS_PER_ROW]}
        for start in range(0, len(buttons), _BUTTONS_PER_ROW)
    ]
    if len(teammates) > _PICKER_SHOWN:
        hidden = len(teammates) - _PICKER_SHOWN
        blocks.append(
            {
                "type": "context",
                "elements": [
                    _text(
                        f"{hidden} more aren't listed. Invite one by name with "
                        "`/invite @name` instead."
                    )
                ],
            }
        )
    return text, blocks


def handed_over(
    *, channel_id: str, bot_user_id: str
) -> tuple[str, list[dict[str, object]]]:
    text = (
        f"Done: <@{bot_user_id}> is in <#{channel_id}>. Mention it to ask it something."
    )
    return text, [{"type": "section", "text": _text(text)}]


def not_handed_over(
    *, channel_id: str, reason: str
) -> tuple[str, list[dict[str, object]]]:
    text = f"I couldn't bring a teammate into <#{channel_id}>: {reason}"
    return text, [{"type": "section", "text": _text(text)}]


def manager_reply(*, bot_user_ids: Sequence[str]) -> str:
    """What the manager says when someone talks to it as if it answered."""
    if not bot_user_ids:
        return (
            "I set teammates up in Slack; I don't answer questions myself. Open my "
            "Home tab to add one."
        )
    ask = ", ".join(f"<@{bot}>" for bot in bot_user_ids[:10])
    return f"I set teammates up in Slack; I don't answer questions myself. Ask {ask}."
