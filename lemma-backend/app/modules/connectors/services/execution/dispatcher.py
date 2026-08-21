"""The single boundary where a connector call is bounded in time.

Two things were previously unbounded or bounded in the wrong place:

* **Discovery had no timeout at all.** ``discover_mcp`` connected to a
  tenant-supplied server with no deadline, inline in the request that created
  the install, so an unresponsive server held that request open until the ASGI
  worker gave up on it.
* **Execution's inner timeouts were dead.** The HTTP executor set 60s behind a
  45s outer bound, so the generic timeout always fired first and the specific
  error was unreachable. Inner deadlines are now derived from the outer one.

Putting both on the dispatcher means every kind is bounded by construction --
a new kind cannot forget to add a timeout.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.errors import OperationExecutionTimeoutError
from app.modules.connectors.domain.kinds import (
    DiscoveredOperation,
    ExecutionRequest,
    ResolvedInstall,
)
from app.modules.connectors.infrastructure.kinds.registry import KindRegistry

logger = get_logger(__name__)

# Per-kind execution ceilings. A constant rather than settings: these express how
# long each kind's upstream can reasonably take, not something an operator tunes
# per environment. `connector_operation_timeout_seconds` remains the env-tunable
# default for anything not listed.
_TIMEOUT_BY_KIND: dict[str, float] = {
    ConnectorKind.HTTP.value: 45.0,
    ConnectorKind.SQL.value: 35.0,
    # MCP servers commonly front slow tools of their own.
    ConnectorKind.MCP.value: 60.0,
    # Composio brokers a second hop out to the real provider.
    ConnectorKind.COMPOSIO.value: 90.0,
    ConnectorKind.PACKAGE.value: 45.0,
}


class KindDispatcher:
    """Routes to a kind's plugin and bounds how long it may take."""

    def __init__(self, registry: KindRegistry):
        self._registry = registry

    def timeout_for(self, kind: ConnectorKind | str) -> float:
        value = kind.value if isinstance(kind, ConnectorKind) else str(kind)
        return _TIMEOUT_BY_KIND.get(
            value, connector_settings.connector_operation_timeout_seconds
        )

    def build_request(
        self,
        *,
        connector_id: str,
        kind: ConnectorKind,
        operation: Any,
        payload: dict[str, Any],
        credentials: dict[str, Any],
        config: dict[str, Any],
        auth_token: str | None = None,
        api_url: str | None = None,
    ) -> ExecutionRequest:
        return ExecutionRequest(
            connector_id=connector_id,
            kind=kind,
            operation=operation,
            payload=payload or {},
            credentials=credentials or {},
            config=config or {},
            deadline_seconds=self.timeout_for(kind),
            auth_token=auth_token,
            api_url=api_url,
        )

    async def execute(self, request: ExecutionRequest) -> Any:
        plugin = self._registry.get(request.kind)
        try:
            return await asyncio.wait_for(
                plugin.executor.execute(request),
                timeout=request.deadline_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning(
                "connector.operation.timeout",
                connector_id=request.connector_id,
                operation_name=request.operation.name,
            )
            raise OperationExecutionTimeoutError(
                f"Operation '{request.operation.name}' timed out after "
                f"{request.deadline_seconds:.0f}s.",
                details={
                    "connector_id": request.connector_id,
                    "timeout_seconds": request.deadline_seconds,
                },
            ) from exc

    async def discover(
        self, install: ResolvedInstall, credentials: dict[str, Any] | None = None
    ) -> list[DiscoveredOperation]:
        """Discover an install's operations, or return none if the kind is static.

        A kind without a discoverer (Composio, vendored packages, a connector
        whose spec was bundled at catalog-import time) has a fixed operation set
        and returns an empty list rather than erroring.
        """
        plugin = self._registry.get(install.kind)
        if plugin.discoverer is None:
            return []
        timeout = connector_settings.connector_discovery_timeout_seconds
        try:
            return await asyncio.wait_for(
                plugin.discoverer.discover(install, credentials), timeout=timeout
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise OperationExecutionTimeoutError(
                f"Discovery for '{install.connector_id}' timed out after "
                f"{timeout:.0f}s.",
                details={
                    "connector_id": install.connector_id,
                    "timeout_seconds": timeout,
                },
            ) from exc
