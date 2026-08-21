"""Where the auth UI lives, as one URL.

The auth site is configured as two halves — an origin (``auth_frontend_url``)
and the path the UI is mounted at (``auth_website_base_path``) — because
SuperTokens is initialised with ``website_domain`` and ``website_base_path``
separately. Consumers *inside* the backend want the origin: CORS, the
SuperTokens init, the Telegram OIDC issuer.

Every consumer *outside* it wants the two already joined. The browser SDK
derives sign-in and callback routes from the pathname of the ``authUrl`` it is
handed, and the CLI opens ``<auth url>/cli/login``. Handed the bare origin they
build ``/`` and ``/callback`` and ``/cli/login`` — none of which exist, because
the routes are all under the base path. This module is that join, in one place,
so each client stops re-deriving it (and the web shell's hand-rolled ``/auth``
append has something to converge on).
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import settings


def apply_auth_base_path(base: str) -> str:
    """``base`` with the auth UI's base path applied, at most once.

    Idempotent on purpose: a deployment that already points its auth URL at the
    mounted path is configured correctly, and must not end up at
    ``/auth/auth``.
    """
    trimmed = base.rstrip("/")
    base_path = settings.auth_website_base_path
    # "/" means the UI is mounted at the origin — there is nothing to join.
    if base_path == "/":
        return trimmed
    if urlparse(trimmed).path.rstrip("/").endswith(base_path):
        return trimmed
    return f"{trimmed}{base_path}"


def auth_ui_url() -> str:
    """The auth UI URL handed to browsers — apps and the web SDK."""
    return apply_auth_base_path(settings.auth_frontend_url)


def cli_auth_ui_url() -> str:
    """The auth UI URL handed to the CLI and to workspace sandboxes.

    ``cli_auth_frontend_url`` exists so local dev can advertise a different host
    to the CLI than the browser canonical one; when it is unset the browser URL
    is the same answer.
    """
    return apply_auth_base_path(
        settings.cli_auth_frontend_url or settings.auth_frontend_url
    )
