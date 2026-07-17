"""Global HTTP exception handlers.

This is the single place where exceptions become HTTP responses. Every error
response uses one envelope::

    {"message": str, "code": str, "details": object | None}

``register_exception_handlers`` is called by ``create_app()``, so the same
handlers are shared by lemma-backend, ``standalone_app`` and lemma-cloud.

Domain errors (``app.core.domain.errors.DomainError`` and its subclasses) carry
their own ``status_code``/``code`` and are translated automatically — controllers
do NOT need to catch them and re-raise as ``HTTPException``. The only reasons to
catch a domain error in a controller are (a) a streaming endpoint that must set
the status before the response body starts, or (b) a genuine status remap.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.core.observability.telemetry import record_exception_on_current_span
from app.core.redaction import redact_text, redact_value

logger = get_logger(__name__)

# Per-(route template, status code) throttle window. Auth denials and rate-limit
# responses can be high-volume; the contract wants an aggregate/state-transition
# signal, not one event per request. Process-local; each replica emits its own
# bounded aggregate (cardinality is bounded by route template x status code).
_THROTTLE_WINDOW_SECONDS = 10.0
_last_throttled_emit: dict[tuple[str, int], float] = {}


def _sanitize_validation_payload(value: object) -> object:
    """Convert non-JSON-safe validation payload values into serializable forms."""
    if isinstance(value, BaseException):
        return {"type": type(value).__name__}
    if isinstance(value, Mapping):
        return {key: _sanitize_validation_payload(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_validation_payload(item) for item in value]
    return value


def _error_body(
    request: Request,
    message: str,
    code: str,
    details: object | None = None,
) -> dict:
    """The unified error envelope shared by every handler."""
    return {
        "message": redact_text(message),
        "code": code,
        "request_id": request.headers.get("x-request-id"),
        "details": redact_value(details),
    }


def _route_template(request: Request) -> str:
    """Normalized route template (e.g. ``/pods/{id}``), never a raw URL/query string."""
    route = request.scope.get("route")
    try:
        if route is not None and hasattr(route, "path_format"):
            return route.path_format
    except Exception:  # pragma: no cover - defensive
        pass
    return redact_text(request.url.path)


def _throttled(
    log_method: Callable[..., None],
    event: str,
    *,
    request: Request,
    status_code: int,
    code: str | None,
    error_type: str,
) -> None:
    """Emit at most one ``event`` per (route, status) per throttle window."""
    route = _route_template(request)
    key = (route, status_code)
    now = time.monotonic()
    last = _last_throttled_emit.get(key)
    if last is not None and now - last < _THROTTLE_WINDOW_SECONDS:
        return
    _last_throttled_emit[key] = now
    payload: dict[str, Any] = {
        "route": route,
        "method": request.method,
        "status_code": status_code,
        "error_type": error_type,
        "request_id": request.headers.get("x-request-id"),
    }
    if code is not None:
        payload["code"] = code
    log_method(event, **payload)


def _log_request_error(
    request: Request,
    *,
    status_code: int,
    code: str | None,
    error_type: str,
) -> None:
    """Severity policy for an HTTP error boundary.

    * 5xx -> ``error`` once (the global boundary is the single place a 5xx is logged).
    * 401/403 (auth denial) -> sampled ``info`` via throttle.
    * 429 (rate limit) -> rate-limited ``warning`` via throttle.
    * other 4xx (validation, not-found, conflict, ...) -> ``debug``; no
      production steady-state event at INFO+.
    """
    if status_code >= 500:
        logger.error(
            "request.error",
            route=_route_template(request),
            method=request.method,
            status_code=status_code,
            code=code,
            error_type=error_type,
            request_id=request.headers.get("x-request-id"),
        )
        return
    if status_code in (401, 403):
        _throttled(
            logger.info,
            "auth.denied",
            request=request,
            status_code=status_code,
            code=code,
            error_type=error_type,
        )
        return
    if status_code == 429:
        _throttled(
            logger.warning,
            "request.rate_limited",
            request=request,
            status_code=status_code,
            code=code,
            error_type=error_type,
        )
        return
    logger.debug(
        "request.error",
        route=_route_template(request),
        method=request.method,
        status_code=status_code,
        code=code,
        error_type=error_type,
        request_id=request.headers.get("x-request-id"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register the unified error handlers on ``app`` (idempotent per app)."""

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError):
        record_exception_on_current_span(
            exc,
            attributes={
                "app.domain_error": True,
                "app.domain_error.code": exc.code,
                "http.response.status_code": exc.status_code,
            },
            mark_span_as_error=exc.status_code >= 500,
        )
        _log_request_error(
            request,
            status_code=exc.status_code,
            code=exc.code,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.message, exc.code, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        errors = _sanitize_validation_payload(exc.errors())
        record_exception_on_current_span(
            exc,
            attributes={
                "app.validation_error": True,
                "http.response.status_code": 422,
            },
            mark_span_as_error=False,
        )
        _log_request_error(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "Request validation failed", "VALIDATION_ERROR", errors
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        record_exception_on_current_span(
            exc,
            attributes={"http.response.status_code": exc.status_code},
            mark_span_as_error=exc.status_code >= 500,
        )
        _log_request_error(
            request,
            status_code=exc.status_code,
            code=f"HTTP_{exc.status_code}",
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                request, str(redact_value(exc.detail)), f"HTTP_{exc.status_code}"
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception):
        record_exception_on_current_span(
            exc,
            attributes={
                "app.unhandled_exception": True,
                "http.response.status_code": 500,
            },
            mark_span_as_error=True,
        )
        # The global boundary logs the 5xx exactly once; the traceback renders as
        # one JSON-escaped ``exception`` string (no physical-line fan-out).
        logger.exception(
            "request.error",
            route=_route_template(request),
            method=request.method,
            status_code=500,
            code="INTERNAL_ERROR",
            error_type=type(exc).__name__,
            request_id=request.headers.get("x-request-id"),
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "Internal server error", "INTERNAL_ERROR"),
        )
