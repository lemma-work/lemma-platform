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

from collections.abc import Mapping, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.core.observability.telemetry import record_exception_on_current_span
from app.core.redaction import redact_text, redact_value

logger = get_logger(__name__)


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
        log_method = logger.error if exc.status_code >= 500 else logger.warning
        log_method(
            "Domain error",
            path=request.url.path,
            method=request.method,
            code=exc.code,
            status_code=exc.status_code,
            message=exc.message,
            details=redact_value(exc.details),
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
        logger.warning(
            "Request validation error",
            path=request.url.path,
            method=request.method,
            status_code=422,
            error_count=len(errors) if isinstance(errors, Sequence) else 0,
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
        log_method = logger.error if exc.status_code >= 500 else logger.info
        log_method(
            "HTTP exception",
            path=request.url.path,
            method=request.method,
            status_code=exc.status_code,
            detail=redact_value(exc.detail),
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
        logger.exception(
            "Unhandled exception",
            path=request.url.path,
            method=request.method,
            status_code=500,
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "Internal server error", "INTERNAL_ERROR"),
        )
