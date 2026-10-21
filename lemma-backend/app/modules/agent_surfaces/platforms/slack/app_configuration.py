"""Slack's app-configuration API: rotating a configuration token, making an app.

The calls Lemma needs to give a pod a Slack app of its own, and nothing else. A
configuration token is a person's, for one workspace, and lasts twelve hours;
its refresh token mints the next pair. Lemma treats every refresh token as
single-use: Slack documents rotation and leaves reuse unspecified, and a
reused one may be refused -- or taken as theft and the chain revoked. So only
one rotation of a stored token may be in flight at once, which the service
arranges (``services/slack_app_provisioning.py``); this module only makes the
calls.

Every token and app secret is a ``SecretStr`` here and in what calls this, so
no ``repr``, log line or traceback with locals prints one. Plaintext is
revealed only as the argument to Slack's SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NoReturn

from pydantic import SecretStr

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfacePlatformError,
    SlackConfigurationTokenRefusedError,
    SlackRateLimitedError,
)
from app.modules.agent_surfaces.platforms.slack.errors import slack_api_error

if TYPE_CHECKING:
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

logger = get_logger(__name__)

_PLATFORM = "SLACK"
#: Shorter than the service's rotation lease, so a rotation that is still
#: running when its lease ends has already been abandoned by the SDK.
SLACK_CALL_TIMEOUT_SECONDS = 20
#: Slack's own lifetime for a configuration token, assumed when a rotation
#: names none.
_ACCESS_TOKEN_LIFETIME = timedelta(hours=12)
#: What Slack answers a refresh token it will never accept again with.
_REFUSED = frozenset(
    {
        "invalid_refresh_token",
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "token_expired",
        "account_inactive",
    }
)
_DEFAULT_RETRY_AFTER_SECONDS = 30


def _client(token: SecretStr | None = None) -> "AsyncWebClient":
    """Slack's client, imported here: serving a request must not load the SDK."""
    from slack_sdk.web.async_client import AsyncWebClient

    return AsyncWebClient(
        token=token.get_secret_value() if token is not None else None,
        timeout=SLACK_CALL_TIMEOUT_SECONDS,
    )


@dataclass(frozen=True)
class RotatedConfigurationToken:
    access_token: SecretStr
    refresh_token: SecretStr
    team_id: str
    slack_user_id: str | None
    expires_at: datetime


@dataclass(frozen=True)
class CreatedSlackApp:
    app_id: str
    client_id: str
    client_secret: SecretStr
    signing_secret: SecretStr


def slack_error_code(exc: "SlackApiError") -> str:
    response = getattr(exc, "response", None)
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return str(data.get("error") or "unknown_error")
    return "unknown_error"


def _retry_after(exc: "SlackApiError") -> int:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return max(1, int(str(raw)))
    except TypeError, ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS


def _platform_error(exc: "SlackApiError", doing: str) -> NoReturn:
    """Slack's refusal as the API says it. ``from None``: the SDK's error keeps
    the request it refused, and the request carries the token."""
    code = slack_error_code(exc)
    if code == "ratelimited":
        raise SlackRateLimitedError(_retry_after(exc)) from None
    raise AgentSurfacePlatformError(_PLATFORM, f"Slack {doing} ({code})") from None


async def rotate_configuration_token(
    refresh_token: SecretStr,
) -> RotatedConfigurationToken:
    """A fresh configuration token, and the refresh token for the next one.

    Raises ``SlackConfigurationTokenRefusedError`` when Slack will never accept
    this refresh token; the caller decides whose mistake that is.
    """
    try:
        response = await _client().tooling_tokens_rotate(
            refresh_token=refresh_token.get_secret_value().strip()
        )
    except slack_api_error() as exc:
        code = slack_error_code(exc)
        if code in _REFUSED:
            raise SlackConfigurationTokenRefusedError(code) from None
        _platform_error(exc, "would not rotate the configuration token")
    data = response.data if isinstance(response.data, dict) else {}
    access, refresh, team_id = (
        str(data.get(key) or "") for key in ("token", "refresh_token", "team_id")
    )
    if not (access and refresh and team_id):
        raise AgentSurfacePlatformError(
            _PLATFORM, "Slack rotated the configuration token but returned no token"
        )
    return RotatedConfigurationToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        team_id=team_id,
        slack_user_id=str(data.get("user_id") or "") or None,
        expires_at=_expiry(data.get("exp")),
    )


def _expiry(exp: object) -> datetime:
    """When the new access token expires. A rotation naming no expiry is given
    Slack's documented twelve hours: reading it as the epoch would make every
    later use rotate again."""
    try:
        seconds = int(str(exp))
    except TypeError, ValueError:
        seconds = 0
    if seconds <= 0:
        return datetime.now(UTC) + _ACCESS_TOKEN_LIFETIME
    return datetime.fromtimestamp(seconds, UTC)


async def workspace_name(access_token: SecretStr) -> str | None:
    """The workspace's name, for showing which one a token is for. Best effort."""
    try:
        response = await _client(access_token).auth_test()
    except slack_api_error() as exc:
        logger.warning(
            "agent_surfaces.slack_app_creation.workspace_name.degraded",
            slack_error=slack_error_code(exc),
        )
        return None
    data = response.data if isinstance(response.data, dict) else {}
    return str(data.get("team") or "") or None


async def create_app(
    access_token: SecretStr, manifest: dict[str, object]
) -> CreatedSlackApp:
    """Make a Slack app from a manifest. Its credentials come back once, here."""
    try:
        # A dict, not a JSON string: the SDK serialises it, and a manifest is
        # small enough that doing it here would only move the same work.
        response = await _client(access_token).apps_manifest_create(
            manifest=dict(manifest)
        )
    except slack_api_error() as exc:
        _platform_error(exc, "would not create the app")
    data = response.data if isinstance(response.data, dict) else {}
    credentials = data.get("credentials")
    credentials = credentials if isinstance(credentials, dict) else {}
    app_id = str(data.get("app_id") or "")
    values = [
        str(credentials.get(key) or "")
        for key in ("client_id", "client_secret", "signing_secret")
    ]
    if not (app_id and all(values)):
        raise AgentSurfacePlatformError(
            _PLATFORM, "Slack created the app but returned no credentials"
        )
    client_id, client_secret, signing_secret = values
    return CreatedSlackApp(
        app_id=app_id,
        client_id=client_id,
        client_secret=SecretStr(client_secret),
        signing_secret=SecretStr(signing_secret),
    )


async def delete_app(access_token: SecretStr, app_id: str) -> bool:
    """Undo ``create_app``. Best effort: an app already gone counts as deleted."""
    try:
        await _client(access_token).apps_manifest_delete(app_id=app_id)
    except slack_api_error() as exc:
        code = slack_error_code(exc)
        if code in {"app_not_found", "invalid_app_id"}:
            return True
        logger.warning(
            "agent_surfaces.slack_app_creation.app_delete.degraded",
            app_id=app_id,
            slack_error=code,
        )
        return False
    return True
