"""CORS for ``/public/web``: each widget's own pages, and never credentials.

The app-wide CORS policy is for Lemma's own frontends: a fixed list of origins,
with credentials. A widget is called from its customers' sites, which no fixed
list can name, and must never be called with credentials -- a visitor's only
credential is the access token the page puts in ``Authorization`` itself. So
this sits where the app-wide ``CORSMiddleware`` sat, hands it every other path
unchanged, and answers ``/public/web/*`` from the widget the path names:

* a pre-flight is answered here, for an origin the widget allows (or the origin
  Lemma's hosted pages are served on), for ``GET`` and ``POST`` with
  ``Authorization`` and ``Content-Type``, cached by the browser for two hours;
* every other response names the page's origin when, and only when, the widget
  allows it -- including the 401, 413 and 500 a page most needs to read. The
  500 is built outside every middleware, so the headers are left on the
  request for the app's error handler to put on it;
* ``Access-Control-Allow-Credentials`` is never sent.

It also caps a public request's body: the sender chooses the size, and a key
copied off a page must not be a way to make the server hold one.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.api.dependencies import get_uow_factory
from app.core.api.exception_handlers import ERROR_CORS_HEADERS
from app.core.public_web import public_web_enabled
from app.modules.agent_surfaces.api.public_dependencies import origin_allowed
from app.modules.agent_surfaces.domain.web_widgets import WidgetAnswer
from app.modules.agent_surfaces.services.widget_directory import widget_by_key

PREFIX = "/public/web/"
MAX_BODY_BYTES = 65_536

_METHODS = frozenset({"GET", "POST"})
_HEADERS = frozenset({"authorization", "content-type"})
_PREFLIGHT_MAX_AGE = "7200"
_EXPOSED = "Retry-After, X-Request-Id"

type OriginCheck = Callable[[str, str], Awaitable[bool]]


class _TooLarge(Exception):
    pass


async def widget_allows(public_key: str, origin: str) -> bool:
    """Whether a page on ``origin`` may read what this widget's key answers."""
    if not public_web_enabled():
        return False
    widget = await widget_by_key(public_key, uow_factory=get_uow_factory())
    if widget is None or widget.answer is WidgetAnswer.OFF:
        return False
    return origin_allowed(widget, origin)


def _public_key(path: str) -> str | None:
    """The key in ``/public/web/{key}/...``; none for the script itself."""
    key, slash, _rest = path[len(PREFIX) :].partition("/")
    return key if key and slash else None


class PublicWebCORSMiddleware:
    """``everything_else`` -- the app-wide ``CORSMiddleware`` -- except under
    ``/public/web/``."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        everything_else: Callable[[ASGIApp], ASGIApp],
        origin_check: OriginCheck = widget_allows,
    ) -> None:
        self.inner = app
        self.origin_check = origin_check
        # `.app` is the next layer down, as on every Starlette middleware, so
        # whatever walks the stack (tests patching the app-wide policy) still
        # finds the CORSMiddleware it wraps.
        self.app = everything_else(app)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        key = _public_key(scope["path"])
        allowed = bool(origin and key and await self.origin_check(key, origin))
        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
            await self._preflight(headers, origin if allowed else None, send)
            return
        if allowed and origin:
            scope.setdefault("state", {})[ERROR_CORS_HEADERS] = _origin_headers(origin)
        await self._serve(scope, receive, send, origin if allowed else None)

    @staticmethod
    async def _preflight(headers: Headers, origin: str | None, send: Send) -> None:
        method = headers["access-control-request-method"].upper()
        asked = {
            name.strip().lower()
            for name in headers.get("access-control-request-headers", "").split(",")
            if name.strip()
        }
        if origin is None or method not in _METHODS or not asked <= _HEADERS:
            await _respond(send, 400, b"Disallowed CORS request", None, "text/plain")
            return
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", origin.encode("latin-1")),
                    (b"access-control-allow-methods", b"GET, POST"),
                    (b"access-control-allow-headers", b"Authorization, Content-Type"),
                    (b"access-control-max-age", _PREFLIGHT_MAX_AGE.encode()),
                    (b"vary", b"Origin"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def _serve(
        self, scope: Scope, receive: Receive, send: Send, origin: str | None
    ) -> None:
        started = False

        async def send_with_origin(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message.setdefault("headers", [])
                _name_origin(MutableHeaders(scope=message), origin)
            await send(message)

        if _declared_length(scope) > MAX_BODY_BYTES:
            await _refuse_too_large(send, origin)
            return
        try:
            await self.inner(scope, _capped(receive), send_with_origin)
        except _TooLarge:
            if not started:
                await _refuse_too_large(send, origin)


def _origin_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Expose-Headers": _EXPOSED,
        "Vary": "Origin",
    }


def _name_origin(headers: MutableHeaders, origin: str | None) -> None:
    if "access-control-allow-credentials" in headers:
        del headers["access-control-allow-credentials"]
    if origin is None:
        return
    headers["access-control-allow-origin"] = origin
    headers["access-control-expose-headers"] = _EXPOSED
    headers.add_vary_header("Origin")


def _declared_length(scope: Scope) -> int:
    try:
        return int(Headers(scope=scope).get("content-length") or 0)
    except ValueError:
        return 0


def _capped(receive: Receive) -> Receive:
    received = 0

    async def capped() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:
                raise _TooLarge
        return message

    return capped


#: The API's usual error envelope, for a refusal made before the app runs.
_TOO_LARGE = json.dumps(
    {
        "message": "That is too long",
        "code": "too_large",
        "request_id": None,
        "details": {"max_bytes": MAX_BODY_BYTES},
    }
).encode()


async def _refuse_too_large(send: Send, origin: str | None) -> None:
    await _respond(send, 413, _TOO_LARGE, origin)


async def _respond(
    send: Send,
    status: int,
    body: bytes,
    origin: str | None,
    media_type: str = "application/json",
) -> None:
    start: Message = {
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", media_type.encode()),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    _name_origin(MutableHeaders(scope=start), origin)
    await send(start)
    await send({"type": "http.response.body", "body": body})
