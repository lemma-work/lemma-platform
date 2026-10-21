"""Reading what Slack sent Lemma's own app, as one of the few things it handles.

The manager answers no questions, so everything it receives is one of a short
list: its Home opened, it was invited somewhere or left, someone wrote to it, a
button on one of its own views was tapped, its "Add to Slack" modal was
submitted, or it was removed. Anything else is read as ``OTHER`` and dropped
-- a delivery to the manager never reaches the paths that start a teammate's
run, or the group-update path that would read its leaving as a teammate's.

The modal's typed values come back apart from the ids, as secrets: they stay
in the webhook request until they are encrypted (``services/slack_manager_form``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

from pydantic import SecretStr

from app.modules.agent_surfaces.domain.slack_manager import (
    AddForm,
    ManagerInput,
    ManagerInputKind,
    ParsedManagerDelivery,
)
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


def _text(value: object) -> str:
    return str(value or "").strip()


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(_text(value)) if _text(value) else None
    except ValueError:
        return None


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


def _other(team_id: str | None, user: str | None = None) -> ParsedManagerDelivery:
    return ParsedManagerDelivery(
        ManagerInput(kind=ManagerInputKind.OTHER, team_id=team_id, user_id=user)
    )


def parse_manager_input(payload: Mapping[str, object]) -> ParsedManagerDelivery:
    payload = _unwrap(payload)
    kind = payload.get("type")
    team_id = _team_id(payload)
    if kind == "event_callback":
        return ParsedManagerDelivery(_event(payload, team_id))
    if kind == "block_actions":
        return ParsedManagerDelivery(_action(payload, team_id))
    if kind == "view_submission":
        return _submission(payload, team_id)
    return _other(team_id)


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
        return ManagerInput(kind=ManagerInputKind.UNINSTALLED, team_id=team_id)
    if event_type == "tokens_revoked":
        return _revocation(event, team_id)
    if event_type == "app_home_opened":
        return ManagerInput(
            kind=ManagerInputKind.HOME_OPENED, team_id=team_id, user_id=user
        )
    if event_type == "channel_left":
        return ManagerInput(
            kind=ManagerInputKind.LEFT_CHANNEL, team_id=team_id, channel_id=channel
        )
    if event_type == "member_joined_channel":
        # Only the manager's own arrival is a moment; a colleague joining is not.
        if not user or user != _manager_bot(payload):
            return ManagerInput(kind=ManagerInputKind.OTHER, team_id=team_id)
        return ManagerInput(
            kind=ManagerInputKind.JOINED_CHANNEL,
            team_id=team_id,
            user_id=_text(event.get("inviter")) or None,
            channel_id=channel,
        )
    return _said(event, team_id, user=user, channel=channel)


def _said(
    event: Mapping[str, object],
    team_id: str | None,
    *,
    user: str | None,
    channel: str | None,
) -> ManagerInput:
    """Something written to the manager, as if it answered."""
    event_type = event.get("type")
    # Bots, edits and joins say nothing to answer.
    if event.get("bot_id") or event.get("subtype"):
        return ManagerInput(kind=ManagerInputKind.OTHER, team_id=team_id)
    if event_type == "app_mention":
        return ManagerInput(
            kind=ManagerInputKind.MENTIONED,
            team_id=team_id,
            user_id=user,
            channel_id=channel,
        )
    if event_type == "message" and event.get("channel_type") == "im":
        return ManagerInput(
            kind=ManagerInputKind.DIRECT_MESSAGE,
            team_id=team_id,
            user_id=user,
            channel_id=channel,
        )
    return ManagerInput(kind=ManagerInputKind.OTHER, team_id=team_id)


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
        return ManagerInput(kind=ManagerInputKind.OTHER, team_id=team_id)
    return ManagerInput(
        kind=ManagerInputKind.UNINSTALLED, team_id=team_id, revoked_bots=revoked
    )


def _teammate(value: str) -> tuple[UUID | None, UUID | None]:
    """``pod_id:agent_id``, as an "Add to Slack" button carries it."""
    pod, _, agent = value.partition(":")
    return _uuid(pod), _uuid(agent)


def _action(payload: Mapping[str, object], team_id: str | None) -> ManagerInput:
    actions = payload.get("actions")
    action = _dict(actions[0]) if isinstance(actions, list) and actions else {}
    action_id = _text(action.get("action_id"))
    user = _text(_dict(payload.get("user")).get("id")) or None
    trigger_id = _text(payload.get("trigger_id")) or None
    response_url = _text(payload.get("response_url")) or None
    container = _dict(payload.get("container"))
    container_channel = (
        _text(container.get("channel_id"))
        or _text(_dict(payload.get("channel")).get("id"))
        or None
    )
    if action_id == ADD_ACTION_ID:
        pod_id, agent_id = _teammate(_text(action.get("value")))
        return ManagerInput(
            kind=ManagerInputKind.ADD_CLICKED,
            team_id=team_id,
            user_id=user,
            trigger_id=trigger_id,
            response_url=response_url,
            pod_id=pod_id,
            agent_id=agent_id,
        )
    if action_id == JOIN_ACTION_ID or action_id.startswith(f"{JOIN_ACTION_ID}:"):
        try:
            value = _dict(json.loads(_text(action.get("value")) or "{}"))
        except ValueError:
            value = {}
        return ManagerInput(
            kind=ManagerInputKind.JOIN_CLICKED,
            team_id=team_id,
            user_id=user,
            response_url=response_url,
            channel_id=_text(value.get("channel")) or None,
            container_channel_id=container_channel,
            surface_id=_uuid(value.get("surface")),
        )
    return ManagerInput(kind=ManagerInputKind.OTHER, team_id=team_id, user_id=user)


def _value(values: Mapping[str, object], block_id: str) -> str:
    return _text(_dict(_dict(values.get(block_id)).get(INPUT_ACTION_ID)).get("value"))


def _submission(
    payload: Mapping[str, object], team_id: str | None
) -> ParsedManagerDelivery:
    view = _dict(payload.get("view"))
    if view.get("callback_id") != ADD_VIEW_CALLBACK_ID:
        return _other(team_id)
    try:
        metadata = _dict(json.loads(_text(view.get("private_metadata")) or "{}"))
    except ValueError:
        metadata = {}
    values = _dict(_dict(view.get("state")).get("values"))
    return ParsedManagerDelivery(
        ManagerInput(
            kind=ManagerInputKind.ADD_SUBMITTED,
            team_id=team_id,
            user_id=_text(_dict(payload.get("user")).get("id")) or None,
            view_id=_text(view.get("id")) or None,
            pod_id=_uuid(metadata.get("pod")),
            agent_id=_uuid(metadata.get("agent")),
        ),
        form=AddForm(
            refresh_token=SecretStr(_value(values, TOKEN_BLOCK_ID)),
            client_id=_value(values, CLIENT_ID_BLOCK_ID),
            client_secret=SecretStr(_value(values, CLIENT_SECRET_BLOCK_ID)),
            signing_secret=SecretStr(_value(values, SIGNING_SECRET_BLOCK_ID)),
        ),
    )
