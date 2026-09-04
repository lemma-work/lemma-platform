"""Which installation a freshly connected GitHub account speaks for.

The install redirect names it in the callback query, and when it does that is
the authority. But it only *happens* on a first install: someone who already has
the App on their account and visits `/apps/{slug}/installations/new` is shown the
configure page and never redirected anywhere. That is not an edge case -- it is
every reconnect, and every second person in an organization where somebody has
already installed it.

So the connect flow uses the ordinary user-authorization endpoint, which always
round-trips a code, and the installation is resolved afterwards from the token
itself. `GET /user/installations` is a user-to-server call and therefore already
scoped to this App: what comes back is exactly the installations of Lemma's App
that this person can see.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.install_binding import (
    CredentialBlob,
    bind_external_ref,
)

logger = get_logger(__name__)

_USER_INSTALLATIONS_URL = "https://api.github.com/user/installations"
_TIMEOUT = 15.0

#: One entry of GitHub's `/user/installations` response. Third-party JSON: only
#: `id` is read from it, and the rest is GitHub's to change.
Installation = dict[str, Any]


def install_url() -> str | None:
    """Where to send someone who has authorized but installed nothing.

    A user token from a GitHub App reaches only the repositories the App is
    installed on, so authorizing without installing produces a token that works,
    belongs to the right person, and can see nothing at all.
    """
    slug = connector_settings.connector_github_app_slug
    return f"https://github.com/apps/{slug}/installations/new" if slug else None


async def _installations(access_token: str) -> list[Installation]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            _USER_INSTALLATIONS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    response.raise_for_status()
    payload = response.json()
    found = payload.get("installations") if isinstance(payload, dict) else None
    return [item for item in (found or []) if isinstance(item, dict)]


def _access_token(credentials: CredentialBlob) -> str | None:
    if isinstance(credentials, dict):
        token = credentials.get("access_token")
    else:
        token = getattr(credentials, "access_token", None)
    reveal = getattr(token, "get_secret_value", None)
    if callable(reveal):
        token = reveal()
    return str(token) if token else None


async def resolve_installation(access_token: str) -> str | None:
    """The single installation this token speaks for, if there is exactly one.

    Ambiguity is left unresolved rather than guessed at. Someone in two
    organizations that both installed the App has two, and binding the account
    to whichever came back first would route the other organization's events at
    them -- the precise failure the per-account binding exists to prevent. An
    unbound account still works for everything that runs as the user; only
    triggers need the installation, and they say so.
    """
    try:
        installations = await _installations(access_token)
    except httpx.HTTPError, ValueError:
        logger.warning(
            "connectors.github_installation.lookup_failed.degraded", exc_info=True
        )
        return None
    ids = [str(item["id"]) for item in installations if item.get("id") is not None]
    if len(ids) == 1:
        return ids[0]
    logger.warning(
        "connectors.github_installation.not_resolved.degraded",
        count=len(ids),
    )
    return None


async def bound_external_ref(
    connector_id: str,
    credentials: CredentialBlob,
    callback_url: str | None = None,
) -> str | None:
    """The tenant to bind an account to, asking GitHub when nothing else says.

    Every other connector is unchanged: this is `bind_external_ref` plus one
    fallback that only GitHub reaches.
    """
    stated = bind_external_ref(connector_id, credentials, callback_url)
    if stated is not None or (connector_id or "").strip().lower() != "github":
        return stated
    token = _access_token(credentials)
    return await resolve_installation(token) if token else None
