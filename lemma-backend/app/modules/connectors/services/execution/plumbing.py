"""Building the kind dispatcher, the request handed to it, and the error
contract on the way back out."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

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


def _upstream_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {"error_type": type(exc).__name__}
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        details["upstream_status"] = status_code
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

    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
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
