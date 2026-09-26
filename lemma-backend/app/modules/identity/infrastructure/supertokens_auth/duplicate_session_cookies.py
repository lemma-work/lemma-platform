"""Clear every stray copy of a session cookie, not only the one SuperTokens guesses.

A browser can hold the same session cookie more than once, because a cookie is
keyed by name *and domain and path*, and this install has minted it in more
than one shape:

* host-only, when ``SESSION_COOKIE_DOMAIN`` is blank -- every release before
  the ``Domain=lemma.localhost`` change, and the sharing overlay today, which
  blanks the domain for as long as sharing is on;
* ``Domain=lemma.localhost``, the rest of the time;
* the refresh token at SuperTokens' narrow ``refresh_token_path`` or at ``/``,
  depending on whether ``RefreshCookieScopeMiddleware`` was widening it -- and
  the narrow path itself moves when the sharing overlay changes the gateway
  path.

Turning sharing on and off therefore leaves two copies of a cookie in the jar,
and both are sent. SuperTokens meets that on a refresh by clearing the copy at
``older_cookie_domain`` and answering **200 with no ``front-token``**. Two
things go wrong with that answer:

1. The clear reaches only one shape: ``older_cookie_domain`` at the narrow
   refresh path (and ``/``, from ``RefreshCookieScopeMiddleware``). A stray at
   any other domain or path is untouched, so every later refresh meets the same
   pair and gets the same 200 -- a loop.
2. Where the *live* scope is host-only (sharing on, ``older_cookie_domain``
   blank) the "older" copy it clears is the live one, keeping the stale one.

And the browser SDK throws on a 200 refresh without ``front-token`` (it takes
it for a proxy stripping the header), which a pod app that refreshes on its
first load reports as being signed out.

So whenever a request carries a duplicate, this clears every shape the stray
could have -- host-only and each parent domain of the host, at every path that
could have sent it -- except the live one, which the server keeps writing, and
it drops a clear SuperTokens aimed at the live shape. The next refresh then
carries one copy of each cookie and answers normally; the client retries once
for exactly that.

Clearing a cookie that does not exist is a no-op, so over-reaching costs a few
headers on a response that is already an anomaly. What it cannot do is tell
which copy the person meant: if the stray was the newer session, they sign in
once. That is the trade SuperTokens' own ``older_cookie_domain`` makes too, and
it beats a refresh that fails forever.
"""

from __future__ import annotations

import ipaddress
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.modules.identity.config import identity_settings

ACCESS_COOKIE = "sAccessToken"
REFRESH_COOKIE = "sRefreshToken"
SESSION_COOKIES = (ACCESS_COOKIE, REFRESH_COOKIE)

_EXPIRED = "expires=Thu, 01 Jan 1970 00:00:00 GMT"


@dataclass(frozen=True)
class CookieScope:
    """Where one copy of a cookie lives. ``domain`` None means host-only."""

    domain: str | None
    path: str


@dataclass(frozen=True)
class LiveCookieShape:
    """The one shape this server writes session cookies in right now."""

    domain: str | None
    refresh_path: str
    secure: bool
    same_site: str
    #: Other paths this server has written the refresh cookie at: SuperTokens'
    #: own narrow ``refresh_token_path``, which the live path replaced.
    known_refresh_paths: tuple[str, ...] = ()

    def live(self, name: str) -> CookieScope:
        # SuperTokens has only ever written the access token at `/`.
        path = self.refresh_path if name == REFRESH_COOKIE else "/"
        return CookieScope(_bare_domain(self.domain), path)


def _bare_domain(domain: str | None) -> str | None:
    # `Domain=.x` and `Domain=x` are the same cookie to every browser.
    if not domain:
        return None
    return domain.strip().lstrip(".").lower() or None


def duplicated_session_cookies(cookie_header: str) -> set[str]:
    """Session cookie names that appear more than once in a ``Cookie`` header."""
    counts = Counter(
        pair.split("=", 1)[0].strip()
        for pair in cookie_header.split(";")
        if "=" in pair
    )
    return {name for name in SESSION_COOKIES if counts[name] > 1}


def _hostname(value: str) -> str | None:
    if not value:
        return None
    netloc = urlsplit(value).netloc if "//" in value else value
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        return None
    host = host.split(":", 1)[0].strip().lower()
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    # An IP address has no parent domain to have scoped a cookie to.
    return None


def candidate_domains(hosts: Iterable[str]) -> list[str | None]:
    """Host-only, then every domain a cookie sent to these hosts could name.

    ``demo.apps.lemma.localhost`` gives the host itself, ``apps.lemma.localhost``
    and ``lemma.localhost``. A single label (``localhost``) is never a cookie
    domain a browser would accept, so it is left out.
    """
    domains: list[str | None] = [None]
    for raw in hosts:
        host = _hostname(raw)
        if host is None:
            continue
        labels = host.split(".")
        for start in range(len(labels) - 1):
            domain = ".".join(labels[start:])
            if domain not in domains:
                domains.append(domain)
    return domains


def path_prefixes(path: str) -> list[str]:
    """``/``, then each segment-boundary prefix of ``path``.

    A cookie is sent only to paths its own path is a prefix of, so a copy that
    arrived on this request lives at one of these.
    """
    prefixes = ["/"]
    current = ""
    for segment in path.split("/"):
        if not segment:
            continue
        current = f"{current}/{segment}"
        prefixes.append(current)
    return prefixes


def stray_scopes(
    name: str,
    shape: LiveCookieShape,
    *,
    hosts: Iterable[str],
    request_path: str,
) -> list[CookieScope]:
    """Every scope a copy of ``name`` could live at, other than the live one."""
    if name == REFRESH_COOKIE:
        paths: list[str] = []
        for candidate in (
            *path_prefixes(request_path),
            *path_prefixes(shape.refresh_path),
            *shape.known_refresh_paths,
        ):
            if candidate not in paths:
                paths.append(candidate)
    else:
        paths = ["/"]
    live = shape.live(name)
    return [
        scope
        for domain in candidate_domains(hosts)
        for path in paths
        if (scope := CookieScope(domain, path)) != live
    ]


def clearing_header(name: str, scope: CookieScope, shape: LiveCookieShape) -> str:
    """A ``Set-Cookie`` value that deletes ``name`` at exactly ``scope``."""
    parts = [f'{name}=""', _EXPIRED, "Max-Age=0"]
    if scope.domain:
        parts.append(f"Domain={scope.domain}")
    parts.extend(["HttpOnly", f"Path={scope.path}", f"SameSite={shape.same_site}"])
    if shape.secure:
        parts.append("Secure")
    return "; ".join(parts)


def _parse_set_cookie(value: str) -> tuple[str, str, CookieScope] | None:
    """``(name, value, scope)`` of one ``Set-Cookie``, or None if unparseable."""
    pieces = [piece.strip() for piece in value.split(";")]
    if not pieces or "=" not in pieces[0]:
        return None
    name, cookie_value = pieces[0].split("=", 1)
    domain: str | None = None
    path = "/"
    for attribute in pieces[1:]:
        key, _, attr_value = attribute.partition("=")
        key = key.strip().lower()
        if key == "domain":
            domain = _bare_domain(attr_value)
        elif key == "path":
            path = attr_value.strip() or "/"
    return name.strip(), cookie_value.strip(), CookieScope(domain, path)


def _clears(cookie_value: str, header: str) -> bool:
    return cookie_value in ("", '""') and "1970" in header


def live_shape_from_supertokens() -> LiveCookieShape | None:
    """The shape SuperTokens is configured to write, or None before it is."""
    # Imported here: the middleware is installed on every app, and the
    # session recipe's import graph is large enough to count against the
    # import budget for a check that only runs on a duplicate cookie.
    from supertokens_python.exceptions import GeneralError
    from supertokens_python.recipe.session.recipe import SessionRecipe

    try:
        config = SessionRecipe.get_instance().config
    except GeneralError:
        # Not initialised: a test app, or a process that serves no sessions.
        # Nothing here writes session cookies, so nothing needs deduplicating.
        return None
    narrow = config.refresh_token_path.get_as_string_dangerous()
    # `RefreshCookieScopeMiddleware` widens every live refresh cookie to `/`
    # when apps call the API on their own origin.
    refresh_path = "/" if settings.app_api_via_app_origin else narrow
    return LiveCookieShape(
        domain=config.cookie_domain,
        refresh_path=refresh_path,
        known_refresh_paths=(narrow,),
        secure=config.cookie_secure,
        # Configured, not derived: SuperTokens' own derivation needs a request.
        # Deletion matches on name, domain and path alone; this only has to be
        # a value the browser will accept the header with.
        same_site=identity_settings.session_cookie_same_site or "lax",
    )


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str:
    return "; ".join(
        value.decode("latin-1") for key, value in headers if key.lower() == name
    )


def rewrite_response_headers(
    headers: list[tuple[bytes, bytes]],
    *,
    status: int,
    shape: LiveCookieShape,
    hosts: Iterable[str],
    request_path: str,
) -> list[tuple[bytes, bytes]]:
    """The response headers, with every stray session cookie cleared.

    A 200 with no ``front-token`` that clears a session cookie is SuperTokens'
    duplicate-cookie answer. Any clear in it aimed at the *live* shape is
    dropped: that copy is the one being kept.
    """
    has_front_token = any(key.lower() == b"front-token" for key, _ in headers)
    duplicate_answer = status == 200 and not has_front_token
    hosts = list(hosts)
    kept: list[tuple[bytes, bytes]] = []
    already: set[tuple[str, CookieScope]] = set()
    for key, value in headers:
        if key.lower() != b"set-cookie":
            kept.append((key, value))
            continue
        text = value.decode("latin-1")
        parsed = _parse_set_cookie(text)
        if parsed is not None and parsed[0] in SESSION_COOKIES:
            name, cookie_value, scope = parsed
            if _clears(cookie_value, text):
                if duplicate_answer and scope == shape.live(name):
                    continue
                already.add((name, scope))
        kept.append((key, value))
    for name in SESSION_COOKIES:
        for scope in stray_scopes(name, shape, hosts=hosts, request_path=request_path):
            if (name, scope) in already:
                continue
            kept.append(
                (b"set-cookie", clearing_header(name, scope, shape).encode("latin-1"))
            )
    return kept


class DuplicateSessionCookieMiddleware:
    """Clear stray session cookies on any request that carries a duplicate.

    Outside the app-host router, so it sees the path the browser used
    (``/_lemma/...`` on an app origin), which is what a stray's path is a
    prefix of.
    """

    def __init__(
        self,
        app: ASGIApp,
        shape: Callable[[], LiveCookieShape | None] = live_shape_from_supertokens,
    ) -> None:
        self.app = app
        self.shape = shape

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_headers: list[tuple[bytes, bytes]] = scope["headers"]
        if not duplicated_session_cookies(_header(request_headers, b"cookie")):
            await self.app(scope, receive, send)
            return
        shape = self.shape()
        if shape is None:
            await self.app(scope, receive, send)
            return

        # The Host the backend was asked for and the Origin the browser is on.
        # They differ behind the macOS app alias, which rewrites Host to the
        # canonical app host while the page stays on `app.lemma.localhost`.
        hosts = [
            _header(request_headers, b"host"),
            _header(request_headers, b"origin"),
        ]
        request_path = scope.get("path") or "/"

        async def rewrite(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = rewrite_response_headers(
                    list(message.get("headers", [])),
                    status=message["status"],
                    shape=shape,
                    hosts=hosts,
                    request_path=request_path,
                )
            await send(message)

        await self.app(scope, receive, rewrite)
