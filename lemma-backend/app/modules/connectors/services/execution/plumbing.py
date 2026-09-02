"""Building the kind dispatcher, the request handed to it, and the error
contract on the way back out."""

from __future__ import annotations

from typing import Any

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
        account_external_ref=resolved.account_external_ref,
    )


# Re-exported so the many callers that import translation from here keep
# working; it lives in `failure_translation` now, which the dispatcher can
# import at module scope without closing a cycle.
from app.modules.connectors.services.execution.failure_translation import (  # noqa: E402
    execution_failures_translated,
)

__all__ = ["build_dispatcher", "execution_request", "execution_failures_translated"]
