"""Where a cookie-authenticated live socket may be opened from.

Browsers apply no same-origin policy to WebSockets and do send cookies on the
handshake, and the socket reads its session with ``anti_csrf_check=False``. So
without this any page anyone visits could open ``/pods/{pod_id}/live`` as the
signed-in person and stream their pod. The ``Origin`` header cannot be set by a
page's script, which is what makes checking it worth anything.

Allowed: the origins HTTP CORS allows -- the frontends, plus the app hosts
(``<slug>.<app_base_domain>``) -- and the API's own. Refused: ``Origin: null``
(an opaque document names no host) and any other origin. A missing Origin is
what a non-browser client sends, so it is let through.

A socket from an app's host is that app's page acting for its viewer, and is
pinned to the app's own pod as an HTTP request is (``core/app_pin``). Only a
cookie session is checked: a bearer or query-string token is a credential its
holder was handed, not one a page picks up by being opened.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from uuid import UUID

from starlette.requests import HTTPConnection

from app.core.app_pin import UnknownAppHost, app_of_connection, is_null_origin
from app.core.config import settings
from app.core.cors import get_allowed_cors_origin_regex, get_allowed_cors_origins
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _origin_of(url: str | None) -> str | None:
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def origin_allowed(origin: str) -> bool:
    """Whether a browser may open a cookie socket from ``origin``."""
    normalized = _origin_of(origin)
    if normalized is None:
        return False
    allowed = {
        _origin_of(candidate)
        for candidate in (*get_allowed_cors_origins(), settings.api_url)
    }
    if normalized in allowed:
        return True
    pattern = get_allowed_cors_origin_regex()
    if not pattern:
        return False
    try:
        # Anchored at both ends whatever the configured string does.
        return re.fullmatch(pattern, origin.strip()) is not None
    except re.error:
        # A misconfigured pattern refuses rather than admits.
        logger.warning("live.origin_pattern_invalid.denied")
        return False


async def cookie_socket_refusal(connection: HTTPConnection) -> str | None:
    """Why a cookie-authenticated socket may not be opened from where it was,
    or None."""
    origin = connection.headers.get("origin")
    if origin is not None:
        if is_null_origin(origin):
            return "A live socket may not be opened from an opaque origin."
        if not origin_allowed(origin):
            return "A live socket may not be opened from this origin."
    try:
        app = await app_of_connection(connection)
    except UnknownAppHost:
        return "This socket comes from the host of an app that does not exist."
    if app is None:
        return None
    raw_pod_id = connection.path_params.get("pod_id")
    try:
        same_pod = UUID(str(raw_pod_id)) == app.pod_id
    except ValueError:
        same_pod = False
    if not same_pod:
        return "A pod app may open a live socket in its own pod only."
    return None


__all__ = ["cookie_socket_refusal", "origin_allowed"]
