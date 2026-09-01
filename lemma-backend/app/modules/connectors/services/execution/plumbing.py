"""Building the kind dispatcher, the request handed to it, and the error
contract on the way back out."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

from app.core.redaction import redact_text

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.services.execution.dispatcher import KindDispatcher


def build_dispatcher(gateway: Any) -> KindDispatcher:
    """A dispatcher for this service.

    Built per service rather than cached process-wide: caching would also pin
    the gateway it was first built with, which makes behaviour depend on which
    service happened to run first. The state that is genuinely expensive -- the
    SQL engine pool and the outbound HTTP client -- is already shared inside the
    executors, so this is cheap.

    The composio and package plugins delegate to ``gateway``, so those paths are
    unchanged; http/sql/mcp reach their own executors. One lookup replaces the
    old provider-gateway-then-descriptor double dispatch, and every kind is
    bounded by a timeout by construction.
    """
    from app.modules.connectors.infrastructure.kinds import build_kind_registry

    return KindDispatcher(
        build_kind_registry(composio_gateway=gateway, package_gateway=gateway)
    )


def execution_request(dispatcher: KindDispatcher, resolved: Any):
    """Translate a resolved plan into the dispatcher's request shape."""
    from app.modules.connectors.domain.connector_operation import ResolvedOperation

    return dispatcher.build_request(
        connector_id=resolved.connector_id,
        kind=ConnectorKind(resolved.kind or ConnectorKind.PACKAGE.value),
        operation=ResolvedOperation(
            name=resolved.operation_name or resolved.operation_execution_name,
            provider_operation_name=resolved.operation_execution_name,
            input_schema=resolved.input_schema,
            execution=resolved.execution,
        ),
        payload=resolved.payload or {},
        credentials=resolved.third_party_credentials,
        config=resolved.connection_config or {},
    )


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


def _upstream_message(exc: Exception) -> str | None:
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
    if not text.strip():
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
    message = _upstream_message(exc)
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
        OperationExecutionNotFoundError,
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
    }.get(status_code)
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
