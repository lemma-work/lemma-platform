"""Widen the refresh cookie's path where apps call the API on their own origin.

SuperTokens scopes the refresh token to exactly one path -- ``<api base
path>/session/refresh`` -- so it is sent on the one request that needs it and
nowhere else. That is a good default and it breaks the moment the same session
has to be refreshed from two different path prefixes.

Which is what ``app_api_via_app_origin`` creates. The frontend refreshes at
``/st/auth/session/refresh``; an app, whose SDK derives its base from the
injected ``apiUrl``, refreshes at ``/_lemma/st/auth/session/refresh``. One
cookie cannot carry two paths, so the app's refresh went out with no cookie and
came back 401. The app worked until its access token expired and then signed
itself out with no way back -- roughly an hour in, which is exactly long enough
to look like something else.

``/`` is the only path that covers both. What that costs: the refresh token
rides along on other requests to the same host instead of just the refresh
endpoint. It stays ``HttpOnly`` and ``SameSite``, every host it can reach is
this install's own backend, and it is the same exposure SuperTokens gives any
deployment whose API base path is ``/``.

Off by default, and only reachable where the setting is on -- so nothing changes
for a deployment whose apps already talk to the API host directly.
"""

from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

# Only the refresh token. The access token is already ``Path=/``; rewriting
# anything else here would be widening cookies nobody asked about.
_REFRESH_COOKIE = "sRefreshToken="
_PATH_ATTRIBUTE = re.compile(r";\s*Path=[^;]*", re.IGNORECASE)
# A cookie being deleted: SuperTokens clears one by re-sending it with an empty
# value and an expiry in 1970.
_CLEARING_COOKIE = re.compile(rf'^\s*{_REFRESH_COOKIE}("")?\s*;', re.IGNORECASE)


def widen_refresh_cookie_path(value: str) -> str:
    """Rewrite one ``Set-Cookie`` value's Path to ``/``, if it is the refresh one.

    Returns the value unchanged for every other cookie, and for a refresh cookie
    that somehow carries no Path -- absent means "the current directory" to a
    browser, which is not something to paper over silently here.

    A cookie being *deleted* is also returned unchanged, and that exception is
    load-bearing. Removing a cookie requires an exact name+domain+path match, so
    the clearing cookie has to go out at the path the doomed cookie actually
    lives at. `older_cookie_domain` emits exactly that, at SuperTokens' own
    narrow `refresh_token_path` -- and widening it to `/` made it match nothing,
    so the stale cookie survived every refresh for ever.

    That is not a hypothetical. An install upgraded across the host-only ->
    `.lemma.localhost` change holds two refresh cookies; SuperTokens answers the
    pair with a 500 and the SDK retries per query. Widening the clearing cookie
    turns that loud failure into a permanent quiet one: the refresh starts
    answering 200 with no `front-token`, and the loop simply stops saying why.
    """
    if not value.lstrip().startswith(_REFRESH_COOKIE):
        return value
    if _CLEARING_COOKIE.match(value):
        return value
    if not _PATH_ATTRIBUTE.search(value):
        return value
    return _PATH_ATTRIBUTE.sub("; Path=/", value, count=1)


class RefreshCookieScopeMiddleware:
    """Apply :func:`widen_refresh_cookie_path` to outgoing responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.app_api_via_app_origin:
            await self.app(scope, receive, send)
            return

        async def rewrite(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (
                        key,
                        widen_refresh_cookie_path(value.decode("latin-1")).encode(
                            "latin-1"
                        ),
                    )
                    if key.lower() == b"set-cookie"
                    else (key, value)
                    for key, value in message["headers"]
                ]
            await send(message)

        await self.app(scope, receive, rewrite)
