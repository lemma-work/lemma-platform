"""Reading what Slack sent Lemma's own app, as one of the few things it handles.

The manager answers no questions, so everything it receives is one of a short
list: its Home opened, it was invited somewhere, someone wrote to it, a button
on one of its own views was tapped, its "Add to Slack" modal was submitted, or
it was removed. Anything else is read as ``OTHER`` and dropped -- a delivery to
the manager must never fall through to the paths that start a teammate's run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.agent_surfaces.platforms.slack.manager_blocks import (
    ADD_ACTION_ID,
    ADD_VIEW_CALLBACK_ID,
    CLIENT_ID_BLOCK_ID,
    CLIENT_SECRET_BLOCK_ID,
    INPUT_ACTION_ID,
    JOIN_ACTION_ID,
    SIGNING_SECRET_BLOCK_ID,
    TOKEN_BLOCK_ID,
)


class ManagerInputKind(StrEnum):
    HOME_OPENED = "HOME_OPENED"
    JOINED_CHANNEL = "JOINED_CHANNEL"
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    MENTIONED = "MENTIONED"
    ADD_CLICKED = "ADD_CLICKED"
    JOIN_CLICKED = "JOIN_CLICKED"
    ADD_SUBMITTED = "ADD_SUBMITTED"
    UNINSTALLED = "UNINSTALLED"
    OTHER = "OTHER"


@dataclass(frozen=True)
class AddForm:
    """What the "Add to Slack" modal was submitted with. Every field optional."""

    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    signing_secret: str = ""

    @property
    def has_own_app(self) -> bool:
        return bool(self.client_id and self.client_secret and self.signing_secret)

    @property
    def has_part_of_own_app(self) -> bool:
        return any((self.client_id, self.client_secret, self.signing_secret))


@dataclass(frozen=True)
class ManagerInput:
    kind: ManagerInputKind
    team_id: str | None = None
    #: The Slack user who did it: the viewer, the inviter, the writer, the tapper.
    user_id: str | None = None
    channel_id: str | None = None
    trigger_id: str | None = None
    response_url: str | None = None
    view_id: str | None = None
    pod_id: str | None = None
    surface_id: str | None = None
    form: AddForm = field(default_factory=AddForm)
    #: UNINSTALLED from ``tokens_revoked``: the bot users whose tokens went.
    #: None for ``app_uninstalled``, which takes every one.
    revoked_bots: tuple[str, ...] | None = None


def _text(value: object) -> str:
    return str(value or "").strip()


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _team_id(payload: Mapping[str, object]) -> str | None:
    team = payload.get("team")
    if isinstance(team, dict) and team.get("id"):
        return _text(team.get("id"))
    return _text(payload.get("team_id")) or None


def _unwrap(payload: Mapping[str, object]) -> Mapping[str, object]:
    for key in ("payload", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def parse_manager_input(payload: Mapping[str, object]) -> ManagerInput:
    payload = _unwrap(payload)
    kind = payload.get("type")
    team_id = _team_id(payload)
    if kind == "event_callback":
        return _event(payload, team_id)
    if kind == "block_actions":
        return _action(payload, team_id)
    if kind == "view_submission":
        return _submission(payload, team_id)
    return ManagerInput(ManagerInputKind.OTHER, team_id=team_id)


def _manager_bot(payload: Mapping[str, object]) -> str:
    authorizations = payload.get("authorizations")
    for authorization in authorizations if isinstance(authorizations, list) else []:
        if isinstance(authorization, dict) and authorization.get("is_bot") is not False:
            user = _text(authorization.get("user_id"))
            if user:
                return user
    return ""


def _event(payload: Mapping[str, object], team_id: str | None) -> ManagerInput:
    event = _dict(payload.get("event"))
    event_type = event.get("type")
    user = _text(event.get("user")) or None
    channel = _text(event.get("channel")) or None
    if event_type == "app_uninstalled":
        return ManagerInput(ManagerInputKind.UNINSTALLED, team_id=team_id)
    if event_type == "tokens_revoked":
        return _revocation(event, team_id)
    if event_type == "app_home_opened":
        return ManagerInput(ManagerInputKind.HOME_OPENED, team_id=team_id, user_id=user)
    if event_type == "member_joined_channel":
        # Only the manager's own arrival is a moment; a colleague joining is not.
        if not user or user != _manager_bot(payload):
            return ManagerInput(ManagerInputKind.OTHER, team_id=team_id)
        return ManagerInput(
            ManagerInputKind.JOINED_CHANNEL,
            team_id=team_id,
            user_id=_text(event.get("inviter")) or None,
            channel_id=channel,
        )
    # Bots, edits and joins say nothing to answer.
    if event.get("bot_id") or event.get("subtype"):
        return ManagerInput(ManagerInputKind.OTHER, team_id=team_id)
    if event_type == "app_mention":
        return ManagerInput(
            ManagerInputKind.MENTIONED,
            team_id=team_id,
            user_id=user,
            channel_id=channel,
        )
    if event_type == "message" and event.get("channel_type") == "im":
        return ManagerInput(
            ManagerInputKind.DIRECT_MESSAGE,
            team_id=team_id,
            user_id=user,
            channel_id=channel,
        )
    return ManagerInput(ManagerInputKind.OTHER, team_id=team_id)


def _revocation(event: Mapping[str, object], team_id: str | None) -> ManagerInput:
    """A member revoking their own sign-in revokes a user token, not the
    manager's: only the bots named under ``tokens.bot`` have lost theirs."""
    bots = _dict(event.get("tokens")).get("bot")
    revoked = (
        tuple(_text(bot) for bot in bots if _text(bot))
        if isinstance(bots, list)
        else ()
    )
    if not revoked:
        return ManagerInput(ManagerInputKind.OTHER, team_id=team_id)
    return ManagerInput(
        ManagerInputKind.UNINSTALLED, team_id=team_id, revoked_bots=revoked
    )


def _action(payload: Mapping[str, object], team_id: str | None) -> ManagerInput:
    actions = payload.get("actions")
    action = _dict(actions[0]) if isinstance(actions, list) and actions else {}
    action_id = _text(action.get("action_id"))
    user = _text(_dict(payload.get("user")).get("id")) or None
    trigger_id = _text(payload.get("trigger_id")) or None
    response_url = _text(payload.get("response_url")) or None
    if action_id == ADD_ACTION_ID:
        return ManagerInput(
            ManagerInputKind.ADD_CLICKED,
            team_id=team_id,
            user_id=user,
            trigger_id=trigger_id,
            response_url=response_url,
            pod_id=_text(action.get("value")) or None,
        )
    if action_id == JOIN_ACTION_ID or action_id.startswith(f"{JOIN_ACTION_ID}:"):
        try:
            value = _dict(json.loads(_text(action.get("value")) or "{}"))
        except ValueError:
            value = {}
        return ManagerInput(
            ManagerInputKind.JOIN_CLICKED,
            team_id=team_id,
            user_id=user,
            trigger_id=trigger_id,
            response_url=response_url,
            channel_id=_text(value.get("channel")) or None,
            surface_id=_text(value.get("surface")) or None,
        )
    return ManagerInput(ManagerInputKind.OTHER, team_id=team_id, user_id=user)


def _value(values: Mapping[str, object], block_id: str) -> str:
    return _text(_dict(_dict(values.get(block_id)).get(INPUT_ACTION_ID)).get("value"))


def _submission(payload: Mapping[str, object], team_id: str | None) -> ManagerInput:
    view = _dict(payload.get("view"))
    if view.get("callback_id") != ADD_VIEW_CALLBACK_ID:
        return ManagerInput(ManagerInputKind.OTHER, team_id=team_id)
    try:
        metadata = _dict(json.loads(_text(view.get("private_metadata")) or "{}"))
    except ValueError:
        metadata = {}
    values = _dict(_dict(view.get("state")).get("values"))
    return ManagerInput(
        ManagerInputKind.ADD_SUBMITTED,
        team_id=team_id,
        user_id=_text(_dict(payload.get("user")).get("id")) or None,
        view_id=_text(view.get("id")) or None,
        pod_id=_text(metadata.get("pod")) or None,
        form=AddForm(
            refresh_token=_value(values, TOKEN_BLOCK_ID),
            client_id=_value(values, CLIENT_ID_BLOCK_ID),
            client_secret=_value(values, CLIENT_SECRET_BLOCK_ID),
            signing_secret=_value(values, SIGNING_SECRET_BLOCK_ID),
        ),
    )
