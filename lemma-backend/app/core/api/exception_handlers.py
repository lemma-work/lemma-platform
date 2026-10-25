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

import re

from collections.abc import Mapping, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.authorization.session_approvals import record_delegated_refusal
from app.core.infrastructure.db.transient_errors import is_transient_database_error
from app.core.domain.errors import DomainError
from app.core.observability.telemetry import record_exception_on_current_span
from app.core.redaction import redact_text, redact_value

#: Where a CORS layer *inside* the app puts the headers this request's response
#: carries, for the one response built outside every middleware: the 500 below,
#: which Starlette sends from its outermost layer. Without them the page that
#: made the request cannot read the error at all.
ERROR_CORS_HEADERS = "lemma_error_cors_headers"
#: The request id the observer chose, for the same 500: it is built from the
#: scope the server handed in, which never saw the header the observer adds.
ERROR_REQUEST_ID = "lemma_error_request_id"
REQUEST_ID_HEADER = "x-request-id"


def _sanitize_validation_payload(value: object) -> object:
    """Convert non-JSON-safe validation payload values into serializable forms."""
    if isinstance(value, BaseException):
        return {"type": type(value).__name__}
    if isinstance(value, Mapping):
        return {key: _sanitize_validation_payload(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_validation_payload(item) for item in value]
    return value


#: What each validation error keeps: where, which rule, and the rule's own
#: words. Everything else pydantic attaches is dropped.
_VALIDATION_ERROR_KEYS = ("loc", "type", "msg")


def _without_echoed_input(errors: object) -> object:
    """Each error, without the value it refused.

    Pydantic attaches the refused value as ``input`` -- the whole body for a
    missing field -- and a request is refused for being wrong, not for being
    harmless: a malformed header map carries its credential, a schedule its
    payload. A client fixing a request already has what it sent; what it
    needs back is where and why. ``ctx`` is kept only for values that are the
    rule's (a length limit, the expected values), never the input itself or
    an exception, whose text can quote it. A validator's ``msg`` is kept only
    when it quotes nothing the request sent.
    """
    if not isinstance(errors, list):
        return errors
    return [_described(error) if isinstance(error, dict) else error for error in errors]


#: The ``ctx`` parameters that are the rule's, never the input's: limits,
#: patterns and the values a field accepts. Anything else -- a validator's
#: ``error``, a union's ``tag`` (the discriminator the request sent) -- is
#: dropped, because its text can quote what was refused.
_RULE_CONTEXT_KEYS = frozenset(
    {
        "max_length",
        "min_length",
        "actual_length",
        "ge",
        "le",
        "gt",
        "lt",
        "multiple_of",
        "max_digits",
        "decimal_places",
        "whole_digits",
        "pattern",
        "expected",
        "expected_tags",
        "discriminator",
        "field_type",
        "class_name",
        "allowed_schemes",
    }
)

#: Error types whose ``msg`` is a validator's own text, which may be built from
#: the value it refused (``ValueError(f"Invalid value: {value}")``). The
#: built-in types' messages are pydantic's templates over ``ctx``.
_VALIDATOR_MESSAGE_FALLBACKS = {
    "value_error": "Value error, the value is not valid",
    "assertion_error": "Assertion failed, the value is not valid",
}

#: Shorter strings are kept: they quote nothing worth hiding, and a one-letter
#: input would otherwise match, and blank, nearly every message.
_MIN_QUOTED_LENGTH = 4

#: An enum constant the request chose (``DATASTORE``, ``AGENT_HOST``) is the
#: schema's own vocabulary, which rule messages name on purpose ("DATASTORE
#: schedules must declare operations"); quoting it reveals nothing.
_ENUM_CONSTANT = re.compile(r"[A-Z][A-Z0-9_]*")


def _input_strings(value: object) -> list[str]:
    """Every string value in a refused input; a mapping's keys are field
    names, the schema's words rather than the request's."""
    if isinstance(value, str):
        if len(value) < _MIN_QUOTED_LENGTH or _ENUM_CONSTANT.fullmatch(value):
            return []
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _input_strings(item)]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [text for item in value for text in _input_strings(item)]
    return []


def _safe_message(error: dict[str, object]) -> object:
    """The error's ``msg``, unless it is a validator's and quotes the input.

    The platform's own validators name the rule ("table_name cannot be
    empty"), and those words are what a client needs, so they are kept. A
    message that contains any string the request sent -- or whose input was
    not supplied to check against -- is replaced by its type's fixed wording.
    """
    message = error.get("msg")
    fallback = _VALIDATOR_MESSAGE_FALLBACKS.get(str(error.get("type")))
    if fallback is None or not isinstance(message, str):
        return message
    if "input" not in error:
        return fallback
    if any(text in message for text in _input_strings(error["input"])):
        return fallback
    return message


def _described(error: dict[str, object]) -> dict[str, object]:
    kept = {key: error[key] for key in _VALIDATION_ERROR_KEYS if key in error}
    if "msg" in kept:
        kept["msg"] = _safe_message(error)
    context = error.get("ctx")
    if isinstance(context, Mapping):
        refused = error.get("input")
        rule = {
            str(key): value
            for key, value in context.items()
            if str(key) in _RULE_CONTEXT_KEYS
            and isinstance(value, (int, float, str, bool))
            and not (isinstance(value, str) and value == refused)
        }
        if rule:
            kept["ctx"] = rule
    return kept


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
        "request_id": _request_id(request),
        "details": redact_value(details),
    }


def _request_id(request: Request) -> str | None:
    state = request.scope.get("state")
    chosen = state.get(ERROR_REQUEST_ID) if isinstance(state, Mapping) else None
    return request.headers.get(REQUEST_ID_HEADER) or (
        chosen if isinstance(chosen, str) else None
    )


def _http_exception_code_and_message(exc: HTTPException) -> tuple[str, str]:
    """The envelope's ``code`` and ``message`` for one ``HTTPException``.

    ``DomainError`` carries a real code and this layer passes it through; an
    ``HTTPException`` has only a status, so the code became ``HTTP_403`` and a
    client matching on ``ACCOUNT_INACTIVE`` matched nothing. Several raisers had
    already anticipated that and put the code in the detail as
    ``{"code": ..., "message": ...}`` -- and nothing read it, so the dict was
    ``str()``-ed whole into ``message`` and shipped as a Python literal with the
    real code sitting inside a string no client parses.

    Reading that convention here fixes every one of them at once. A plain string
    detail keeps ``HTTP_<status>``, which is the honest answer: nobody named a
    code, so there is none to report.
    """
    detail = exc.detail
    if isinstance(detail, Mapping):
        code = detail.get("code")
        message = detail.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return code, str(redact_value(message))
    return f"HTTP_{exc.status_code}", str(redact_value(detail))


def _record_request_failure(
    request: Request,
    *,
    code: str,
    error_type: str,
    exception: BaseException | None = None,
) -> None:
    """Pass bounded failure metadata to the outer request observer.

    The observer owns the single terminal request log, avoiding duplicate
    records from exception handlers and middleware.

    ``exception`` is what the observer turns into ``exc_info``, so passing it
    decides whether the failure is diagnosable. The rule is the status code,
    and only the status code:

    * **4xx** — the message is written for the client and may quote what the
      client sent. It stays out of the log entirely; the code and type are
      enough to count them.
    * **5xx** — a server bug, whoever raised it. The message and stack are ours
      and the log is the only place they exist, because the response is a fixed
      envelope by design.

    A 500 raised as a ``DomainError`` used to be the exception to that rule,
    for no reason anyone recorded: it reached the client as the same opaque
    envelope as an unhandled one but reached the log as a bare error type. That
    is how eleven upload failures became undiagnosable — the wrapper said
    "Failed to upload file content" and the ``__cause__`` that knew why was
    unwound before anyone read it.
    """
    state = request.scope.setdefault("state", {})
    state["lemma_error_code"] = code
    state["lemma_error_type"] = error_type
    if exception is not None:
        state["lemma_exception"] = exception


def _unhandled_error_headers(request: Request) -> dict[str, str]:
    """What the 500 carries that the layers it was built outside would add.

    The CORS headers, and the request id: the observer stamps every response
    that passes through it, and this one does not.
    """
    cors = request.scope.get("state", {}).get(ERROR_CORS_HEADERS)
    headers = dict(cors) if isinstance(cors, Mapping) else {}
    request_id = _request_id(request)
    if request_id is not None:
        headers[REQUEST_ID_HEADER] = request_id
    return headers


#: Seconds a client is asked to wait before retrying a request the database
#: could not serve.
_DATABASE_RETRY_AFTER_SECONDS = 2


def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """503, retryable: the database could not take the request, nothing failed.

    A refused connection, a full server, an empty pool or a deadlock says
    nothing about the request; a 500 told clients and monitors it had broken,
    and a datastore mapper called a deadlock a 400 validation error.
    """
    record_exception_on_current_span(
        exc,
        attributes={"app.database_unavailable": True, "http.response.status_code": 503},
        mark_span_as_error=True,
    )
    _record_request_failure(
        request,
        code="DATABASE_UNAVAILABLE",
        error_type=type(exc).__name__,
        exception=exc,
    )
    headers = _unhandled_error_headers(request)
    headers["Retry-After"] = str(_DATABASE_RETRY_AFTER_SECONDS)
    return JSONResponse(
        status_code=503,
        content=_error_body(
            request,
            "The service is briefly unavailable. Try again in a moment.",
            "DATABASE_UNAVAILABLE",
        ),
        headers=headers,
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
        _record_request_failure(
            request,
            code=exc.code,
            error_type=type(exc).__name__,
            exception=exc if exc.status_code >= 500 else None,
        )
        claims = getattr(request.state, "delegation_claims", None)
        if claims is not None and exc.status_code == 403:
            # A workload's token refused something a person could approve: the
            # sandbox CLI's refusals, recorded from the refusal itself so an
            # approval of the command lends them -- never the model's account
            # of them (H6).
            await record_delegated_refusal(claims, code=exc.code, details=exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.message, exc.code, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        errors = _without_echoed_input(_sanitize_validation_payload(exc.errors()))
        record_exception_on_current_span(
            exc,
            attributes={
                "app.validation_error": True,
                "http.response.status_code": 422,
            },
            mark_span_as_error=False,
        )
        _record_request_failure(
            request, code="VALIDATION_ERROR", error_type=type(exc).__name__
        )
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "Request validation failed", "VALIDATION_ERROR", errors
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        code, message = _http_exception_code_and_message(exc)
        record_exception_on_current_span(
            exc,
            attributes={"http.response.status_code": exc.status_code},
            mark_span_as_error=exc.status_code >= 500,
        )
        _record_request_failure(
            request,
            code=code,
            error_type=type(exc).__name__,
            exception=exc if exc.status_code >= 500 else None,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, message, code),
            # `exc.headers` was dropped here, and some statuses are not
            # answerable without them: a 416 has to carry `Content-Range` to
            # tell a resuming download the real size, and a 401 that omits
            # `WWW-Authenticate` is not a challenge. Raising an `HTTPException`
            # with headers looked like it worked -- the status was right and
            # the headers silently went nowhere.
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception):
        if is_transient_database_error(exc):
            return _database_unavailable(request, exc)
        record_exception_on_current_span(
            exc,
            attributes={
                "app.unhandled_exception": True,
                "http.response.status_code": 500,
            },
            mark_span_as_error=True,
        )
        _record_request_failure(
            request,
            code="INTERNAL_ERROR",
            error_type=type(exc).__name__,
            exception=exc,
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "Internal server error", "INTERNAL_ERROR"),
            headers=_unhandled_error_headers(request),
        )
