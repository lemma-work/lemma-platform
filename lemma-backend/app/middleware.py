"""The three ASGI middlewares the application installs.

Split out of `app.py`, which was 999 lines holding the lifespan, these three
classes, the OpenAPI post-processing and a 382-line `create_app`. They are
self-contained: each takes the ASGI app and a little configuration, and none of
them touches the rest of that file.
"""

import asyncio
import re
import time
import uuid
from opentelemetry import metrics
from fastapi.responses import JSONResponse

from app.core.domain.errors import PayloadTooLargeError
from starlette.types import Scope

from app.core.config import settings
from app.core.origin import origin_for_path, origin_scope, resolve_client_identity
from app.core.log.log import get_logger

from app.core.request_context import (
    bind_request_context,
)

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
http_request_count = meter.create_counter("lemma.http.server.requests")
http_request_duration = meter.create_histogram("lemma.http.server.duration_ms")

OPENAPI_SCHEMA_RENAMES = {
    "fastapi___compat__v2__Body_file__upload": "DatastoreFileUploadRequest",
    "fastapi___compat__v2__Body_icon__upload": "IconUploadRequest",
    "fastapi___compat__v2__Body_app__bundle__upload": "AppBundleUploadRequest",
}


_UNMATCHED_ROUTE = "unmatched"


class TrailingSlashMiddleware:
    """Treat ``/things/`` as ``/things`` so a stray slash is not a 404.

    Mutates the scope in place rather than copying it, for the same reason
    documented in ``apps/api/host_routing.py``: the router records the matched
    route by writing ``scope["route"]``, and :class:`RequestObserverMiddleware`
    reads it from *outside* this middleware. With a copy the router wrote to an
    object the observer never saw, so every request whose path ended in a slash
    was logged as ``route: "unmatched"`` regardless of what it actually matched
    or returned -- silently seeding the bucket that a fixed-cost class of slow
    "unmatched" 404s was being investigated in.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path != "/" and path.endswith("/"):
            scope["path"] = path.rstrip("/")

        await self.app(scope, receive, send)


class RequestObserverMiddleware:
    """Bind HTTP correlation, emit bounded terminal signals, and record metrics."""

    HEADER = b"x-request-id"
    # How the work arrived, per docs/design/product-analytics.md. Resolved once
    # here so every downstream emit reads it from context rather than guessing.
    CLIENT_HEADER = b"x-lemma-client"
    REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
    SLOW_SECONDS = 2.0
    QUIET_PATHS = frozenset(
        {"/health", "/health/live", "/health/ready", "/health/capabilities", "/livez"}
    )
    # Routes that are supposed to take a long time. A long poll answers when it
    # has news or when its hold expires, so a slow one is the design working:
    # every completed idle poll logged a warning, and on one local stack 311 of
    # 314 slow-request warnings were healthy 25-second polls. A warning that
    # fires on the normal case is not a signal, and it buried the three that
    # meant something.
    HELD_ROUTES = frozenset({"/agent-host/poll"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        existing = next((v for k, v in headers if k.lower() == self.HEADER), None)
        inbound = existing.decode("latin-1") if existing is not None else ""
        if self.REQUEST_ID_RE.fullmatch(inbound):
            request_id = inbound
        else:
            request_id = uuid.uuid4().hex

        correlation_id = uuid.uuid7()
        scope = dict(scope)
        scope["headers"] = [
            (key, value) for key, value in headers if key.lower() != self.HEADER
        ] + [(self.HEADER, request_id.encode("ascii"))]
        scope.setdefault("state", {})

        started_at = time.perf_counter()
        response_started_at: float | None = None
        status_code = 500
        content_type = ""

        async def send_with_request_id(message):
            nonlocal response_started_at, status_code, content_type
            if message["type"] == "http.response.start":
                response_started_at = time.perf_counter()
                status_code = int(message.get("status", 500))
                raw_headers = [
                    (key, value)
                    for key, value in list(message.get("headers") or [])
                    if key.lower() != self.HEADER
                ]
                content_type = next(
                    (
                        value.decode("latin-1").lower()
                        for key, value in raw_headers
                        if key.lower() == b"content-type"
                    ),
                    "",
                )
                raw_headers.append((self.HEADER, request_id.encode("ascii")))
                message = {**message, "headers": raw_headers}
            await send(message)

        client_header = next(
            (v for k, v in headers if k.lower() == self.CLIENT_HEADER), None
        )
        # The mount point wins over the header where the route itself settles
        # the question: an MCP caller sends no Lemma client header.
        resolved_origin = origin_for_path(scope.get("path") or "") or (
            resolve_client_identity(
                client_header.decode("latin-1", "replace") if client_header else None
            ).origin
        )

        caught: Exception | None = None
        cancelled = False
        with (
            bind_request_context(request_id=request_id, correlation_id=correlation_id),
            origin_scope(resolved_origin),
        ):
            try:
                await self.app(scope, receive, send_with_request_id)
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as exc:
                caught = exc
                raise
            finally:
                finished_at = time.perf_counter()
                duration_ms = round((finished_at - started_at) * 1000, 1)
                route = self._route_template(scope)
                attributes = {
                    "http.request.method": str(scope.get("method", "UNKNOWN")),
                    "http.route": route,
                    # The exact code, not the class. The FastAPI instrumentation's
                    # own histogram records exact codes but no route, and this
                    # counter records the route -- matching the vocabularies is
                    # what lets a dashboard join them into per-route error rate.
                    # Cardinality is bounded by the codes we actually return.
                    "http.response.status_code": status_code,
                }
                http_request_count.add(1, attributes)
                http_request_duration.record(duration_ms, attributes)

                if str(scope.get("path", "")) in self.QUIET_PATHS:
                    continue_logging = False
                else:
                    continue_logging = True
                if continue_logging and not cancelled:
                    state = scope.get("state") or {}
                    recorded = state.get("lemma_exception")
                    failure = caught or recorded
                    fields = {
                        "method": str(scope.get("method", "UNKNOWN")),
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    }
                    # "unmatched" names no request, so a slow or failing one in
                    # that bucket cannot be investigated at all: two separate
                    # passes at a fixed-cost class of slow `unmatched` 404s
                    # failed for exactly this reason. Populated only for that
                    # bucket -- a real route template is already the identity,
                    # and raw paths are unbounded.
                    fields["path"] = (
                        str(scope.get("path", ""))[:120]
                        if route == _UNMATCHED_ROUTE
                        else ""
                    )
                    if status_code >= 500 or caught is not None:
                        fields["error_type"] = state.get(
                            "lemma_error_type",
                            type(failure).__name__ if failure else "HTTPError",
                        )
                        fields["error_code"] = state.get(
                            "lemma_error_code", "INTERNAL_ERROR"
                        )
                        exc_info = (
                            (type(failure), failure, failure.__traceback__)
                            if isinstance(failure, BaseException)
                            else None
                        )
                        logger.error(
                            "http.request.failed",
                            method=fields["method"],
                            route=fields["route"],
                            status_code=fields["status_code"],
                            duration_ms=fields["duration_ms"],
                            error_type=fields["error_type"],
                            error_code=fields["error_code"],
                            exc_info=exc_info,
                            path=fields["path"],
                        )
                    elif status_code == 429:
                        logger.warning(
                            "http.request.rate_limited",
                            method=fields["method"],
                            route=fields["route"],
                            status_code=fields["status_code"],
                            duration_ms=fields["duration_ms"],
                        )
                    else:
                        streaming = content_type.startswith("text/event-stream")
                        elapsed = (
                            (response_started_at - started_at)
                            if streaming and response_started_at is not None
                            else (finished_at - started_at)
                        )
                        if (
                            elapsed >= self.SLOW_SECONDS
                            and fields["route"] not in self.HELD_ROUTES
                        ):
                            fields["duration_ms"] = round(elapsed * 1000, 1)
                            fields["latency_kind"] = (
                                "time_to_first_byte" if streaming else "total"
                            )
                            logger.warning(
                                "http.request.slow",
                                method=fields["method"],
                                route=fields["route"],
                                status_code=fields["status_code"],
                                duration_ms=fields["duration_ms"],
                                latency_kind=fields["latency_kind"],
                                path=fields["path"],
                            )
                        elif settings.local_http_access_logs_enabled:
                            logger.info(
                                "http.request.local_completed",
                                method=fields["method"],
                                route=fields["route"],
                                status_code=fields["status_code"],
                                duration_ms=fields["duration_ms"],
                            )
                        else:
                            logger.debug(
                                "http.request.completed",
                                method=fields["method"],
                                route=fields["route"],
                                status_code=fields["status_code"],
                                duration_ms=fields["duration_ms"],
                            )

    @staticmethod
    def _route_template(scope: Scope) -> str:
        route = scope.get("route")
        value = getattr(route, "path_format", None) or getattr(route, "path", None)
        return value if isinstance(value, str) else _UNMATCHED_ROUTE


# Compatibility name retained for imports and generated SDK tests.
RequestIdMiddleware = RequestObserverMiddleware


class RequestBodyLimitMiddleware:
    """Enforce a byte ceiling without trusting the Content-Length header."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or None
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._send_too_large(scope, receive, send, request_id)
                    return
            except ValueError:
                pass

        received = 0

        async def receive_limited():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLargeError(max_bytes=self.max_bytes)
            return message

        try:
            await self.app(scope, receive_limited, send)
        except PayloadTooLargeError:
            await self._send_too_large(scope, receive, send, request_id)

    async def _send_too_large(self, scope, receive, send, request_id):
        response = JSONResponse(
            status_code=413,
            content={
                "message": "request exceeds the maximum allowed size",
                "code": "UPLOAD_TOO_LARGE",
                "request_id": request_id,
                "details": {"field": "request", "max_bytes": self.max_bytes},
            },
        )
        await response(scope, receive, send)


#: The name this middleware had before it also bound correlation and origin.
RequestIdMiddleware = RequestObserverMiddleware
