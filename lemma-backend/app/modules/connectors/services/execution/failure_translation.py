"""Turning whatever an executor raises into an honest domain error.

Split out of ``plumbing`` because the two halves point in opposite directions.
``plumbing`` builds a dispatcher, so it imports one; the dispatcher needs this
translation, so it imported ``plumbing`` back -- a cycle, deferred to call time
by an import inside a function and flagged as one by ``py/cyclic-import``.

Nothing here knows about a dispatcher. It reads an exception and decides whose
fault it describes, which is why the seam is here and not somewhere chosen to
satisfy the query.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

from app.core.redaction import redact_text


def _upstream_status(exc: Exception) -> int | None:
    """The provider's status code, however this exception happens to carry it.

    Our executors put it on the exception. `httpx.HTTPStatusError` -- which
    reaches here from discovery, where the raw client error is what escapes --
    puts it on the response instead.
    """
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response_status = getattr(getattr(exc, "response", None), "status_code", None)
    return response_status if isinstance(response_status, int) else None


# Generous: the point is to read the provider's actual error. Bounded only
# because a provider may answer with an entire HTML page, and that does not
# belong in a JSON error body.
_UPSTREAM_MESSAGE_LIMIT = 2000


def _response_text(exc: Exception) -> str | None:
    """The response body, when there is one that can be read.

    `.text` is a property that *raises* on a streaming response nobody has read
    -- and MCP speaks streamable HTTP, so that is the common case here, not an
    exotic one. `getattr` does not help: it only swallows a missing attribute,
    not an exception raised by a property that exists.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        return response.text
    except httpx.ResponseNotRead:
        return None


def upstream_message(exc: Exception) -> str | None:
    """What the provider said, passed through.

    Only *our* internals are hidden. A provider's own error is the thing that
    explains the failure -- "invalid_scope", "repository not found", "token
    expired" -- and dropping it left a caller with a status code and no way to
    tell those apart.

    It goes through `redact_text` on the way, which is the same pass the log
    pipeline already makes. It only matches token-shaped text, so a normal
    provider error arrives unchanged; it is here for the gateway that echoes a
    request header back in its error page.
    """
    body = _response_text(exc)
    if isinstance(body, str) and body.strip():
        text = body
    elif _upstream_status(exc) is not None:
        # An executor raised it and attached the provider's status, so its
        # message is the provider's answer rather than ours.
        text = str(exc)
    else:
        # Nothing says this came from upstream, so it is one of ours and its
        # text stays here. That is the whole distinction: a provider's error
        # explains the failure, ours would only leak how we are built.
        return None
    return redacted_upstream_text(text)


def redacted_upstream_text(text: str | None) -> str | None:
    """Provider text as it may leave the system: redacted, then bounded.

    Shared with the vendored-package gateway, which reaches its own decision
    about *whether* an exception carries the provider's answer -- its clients
    do not attach a status code -- but must scrub and cap it the same way when
    it does.
    """
    if not text or not text.strip():
        return None
    redacted = redact_text(text.strip())
    if len(redacted) > _UPSTREAM_MESSAGE_LIMIT:
        redacted = redacted[:_UPSTREAM_MESSAGE_LIMIT] + "…"
    return redacted


def _upstream_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {"error_type": type(exc).__name__}
    status_code = _upstream_status(exc)
    if isinstance(status_code, int):
        details["upstream_status"] = status_code
    message = upstream_message(exc)
    if message:
        details["upstream_message"] = message
    code = getattr(exc, "code", None)
    if isinstance(code, str) and len(code) <= 100:
        details["upstream_code"] = code
    return details


def _status_classified(exc: Exception):
    """The domain error an upstream HTTP status deserves, if it carries one.

    The http/sql/mcp executors raise their own exception types carrying the
    provider's status code. Without this they would all land in the catch-all
    below and read as "our fault, 500" -- so a caller could not tell a repo that
    does not exist from a connector that is broken, which is the difference
    between a normal branch and a failed publish. The package and Composio
    gateways already classify their own errors this way; this gives the same
    contract to every other kind.
    """
    from app.modules.connectors.domain.errors import (
        OperationExecutionAccessDeniedError,
        OperationExecutionInfrastructureError,
        OperationExecutionNotFoundError,
        OperationExecutionRateLimitedError,
        OperationExecutionUnauthorizedError,
        OperationExecutionValidationError,
    )

    status_code = _upstream_status(exc)
    if status_code is None:
        return None
    error_cls = {
        400: OperationExecutionValidationError,
        401: OperationExecutionUnauthorizedError,
        403: OperationExecutionAccessDeniedError,
        404: OperationExecutionNotFoundError,
        422: OperationExecutionValidationError,
        # The provider is healthy and answering; the caller is asking too
        # often. Deliberately not an infrastructure error -- see the class,
        # which exists for this and was reachable only from Composio, so an
        # http/sql/mcp 429 became a 500 with no `retry_after` and an agent
        # retried straight back into the limit.
        429: OperationExecutionRateLimitedError,
    }.get(status_code)
    if error_cls is None and 500 <= status_code < 600:
        # A provider outage, and the only classification the breaker counts.
        # Without this every 5xx fell through to the catch-all as
        # `OperationExecutionError` -- our own 500 -- so the breaker could open
        # on a refused connection but never on a provider returning 503 to
        # everything, which is the case its docstring describes. It also filed
        # every third-party outage under Lemma's own error budget.
        error_cls = OperationExecutionInfrastructureError
    if error_cls is None:
        return None
    # The message is fixed by the error class; the exception's own text may
    # carry provider request bodies or credentials and never travels.
    return error_cls("", details=_upstream_details(exc))


@contextlib.contextmanager
def execution_failures_translated():
    """Turn whatever escapes an executor into an honest domain error.

    Transport failures are transient and say so. A failure the provider itself
    described with a status code is reported as that. Anything else is a fault
    on this side: it is still bounded here, so no traceback and no upstream
    message reaches the caller, but it is not reported as a provider outage --
    that invites a retry which cannot succeed, and files our own bug under
    someone else's name.
    """
    from app.modules.connectors.domain.errors import (
        ConnectorDomainError,
        OperationExecutionError,
        OperationExecutionInfrastructureError,
    )

    try:
        yield
    except ConnectorDomainError:
        raise
    except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
        # A status the provider chose is an answer, not an outage. `HTTPStatusError`
        # is an `HTTPError`, so without this a 401 from a tenant's server was
        # reported as "temporarily unavailable" -- inviting exactly the retry this
        # function's docstring says not to invite.
        classified = _status_classified(exc)
        if classified is not None:
            raise classified from exc
        raise OperationExecutionInfrastructureError(
            "Connector provider is temporarily unavailable.",
            details=_upstream_details(exc),
        ) from exc
    except Exception as exc:
        classified = _status_classified(exc)
        if classified is not None:
            raise classified from exc
        raise OperationExecutionError(
            "The connector operation could not be completed.",
            details=_upstream_details(exc),
        ) from exc
