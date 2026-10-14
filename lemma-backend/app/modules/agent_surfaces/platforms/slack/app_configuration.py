"""Slack's app-configuration API: rotating a configuration token, making an app.

The three calls Lemma needs to give a pod a Slack app of its own, and nothing
else. A configuration token is a person's, for one workspace, and lasts twelve
hours; its refresh token mints the next pair. Slack's docs leave reuse of a
refresh token open, and in a test workspace an old one kept working after a
newer one had been issued -- so rotating is not a race, but the newest pair is
still the one kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from typing import TYPE_CHECKING

from app.modules.agent_surfaces.platforms.slack.errors import slack_api_error
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfacePlatformError,
    AgentSurfaceValidationError,
)

if TYPE_CHECKING:
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

_PLATFORM = "SLACK"


def _client(token: str | None = None) -> "AsyncWebClient":
    """Slack's client, imported here: serving a request must not load the SDK."""
    from slack_sdk.web.async_client import AsyncWebClient

    return AsyncWebClient(token=token)


@dataclass(frozen=True)
class RotatedConfigurationToken:
    access_token: str
    refresh_token: str
    team_id: str
    slack_user_id: str | None
    expires_at: datetime


@dataclass(frozen=True)
class CreatedSlackApp:
    app_id: str
    client_id: str
    client_secret: str
    signing_secret: str


def _slack_error(exc: "SlackApiError") -> str:
    response = getattr(exc, "response", None)
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return str(data.get("error") or "unknown_error")
    return "unknown_error"


async def rotate_configuration_token(refresh_token: str) -> RotatedConfigurationToken:
    """A fresh configuration token, and the refresh token for the next one.

    A refresh token Slack refuses is the caller's mistake (pasted the access
    token, or one from a deleted app), so it is a validation error, not ours.
    """
    try:
        response = await _client().tooling_tokens_rotate(
            refresh_token=refresh_token.strip()
        )
    except slack_api_error() as exc:
        raise AgentSurfaceValidationError(
            "Slack did not accept that refresh token "
            f"({_slack_error(exc)}). Generate a new configuration token on "
            "api.slack.com/apps and paste its refresh token."
        ) from exc
    data = response.data if isinstance(response.data, dict) else {}
    access, refresh, team_id = (
        str(data.get(key) or "") for key in ("token", "refresh_token", "team_id")
    )
    if not (access and refresh and team_id):
        raise AgentSurfacePlatformError(
            _PLATFORM, "Slack rotated the configuration token but returned no token"
        )
    return RotatedConfigurationToken(
        access_token=access,
        refresh_token=refresh,
        team_id=team_id,
        slack_user_id=str(data.get("user_id") or "") or None,
        expires_at=datetime.fromtimestamp(int(data.get("exp") or 0), timezone.utc),
    )


async def workspace_name(access_token: str) -> str | None:
    """The workspace's name, for showing which one a token is for. Best effort."""
    try:
        response = await _client(access_token).auth_test()
    except slack_api_error():
        return None
    data = response.data if isinstance(response.data, dict) else {}
    return str(data.get("team") or "") or None


async def create_app(access_token: str, manifest: dict[str, object]) -> CreatedSlackApp:
    """Make a Slack app from a manifest. Its credentials come back once, here."""
    try:
        # A dict, not a JSON string: the SDK serialises it, and a manifest is
        # small enough that doing it here would only move the same work.
        response = await _client(access_token).apps_manifest_create(
            manifest=dict(manifest)
        )
    except slack_api_error() as exc:
        raise AgentSurfacePlatformError(
            _PLATFORM, f"Slack would not create the app ({_slack_error(exc)})"
        ) from exc
    data = response.data if isinstance(response.data, dict) else {}
    credentials = data.get("credentials")
    credentials = credentials if isinstance(credentials, dict) else {}
    created = CreatedSlackApp(
        app_id=str(data.get("app_id") or ""),
        client_id=str(credentials.get("client_id") or ""),
        client_secret=str(credentials.get("client_secret") or ""),
        signing_secret=str(credentials.get("signing_secret") or ""),
    )
    if not all(
        (
            created.app_id,
            created.client_id,
            created.client_secret,
            created.signing_secret,
        )
    ):
        raise AgentSurfacePlatformError(
            _PLATFORM, "Slack created the app but returned no credentials"
        )
    return created


async def delete_app(access_token: str, app_id: str) -> bool:
    """Undo `create_app` when Lemma could not keep what it made. Best effort."""
    try:
        await _client(access_token).apps_manifest_delete(app_id=app_id)
    except slack_api_error():
        return False
    return True
