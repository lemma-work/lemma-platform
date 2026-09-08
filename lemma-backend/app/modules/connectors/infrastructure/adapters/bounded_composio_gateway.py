"""Bounds the one gateway left: Composio.

There were two, and this chose between them on the legacy provider vocabulary --
``COMPOSIO`` to the Composio SDK, anything else to the vendored connector
clients. The vendored clients are gone and every other kind reaches its executor
through the kind registry, so the routing is gone with them and what remains is
the timeout.

That is not incidental. The Composio SDK call is offloaded to a thread, so it
never blocks the loop -- but without a ceiling a client-abandoned request runs
forever, holding a database connection and a thread slot until both pools
exhaust and the backend wedges. `ComposioOperationGateway` deliberately does not
wrap itself, and says so, because the wrapper has to be outside the offload to
cut it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.errors import (
    ConnectorNotFoundError,
    OperationExecutionTimeoutError,
)
from app.modules.connectors.domain.ports import (
    AppOperationGatewayPort,
    ConnectorRepositoryPort,
)
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)

logger = get_logger(__name__)


class BoundedComposioGateway(AppOperationGatewayPort):
    def __init__(
        self,
        *,
        connector_repository: ConnectorRepositoryPort,
        composio_gateway: ComposioOperationGateway | None = None,
    ):
        self._connector_repository = connector_repository
        self._composio_gateway = composio_gateway or ComposioOperationGateway()

    async def _get_connector(self, connector_id: str) -> ConnectorEntity:
        connector = await self._connector_repository.get(connector_id)
        if not connector:
            raise ConnectorNotFoundError(connector_id)
        return connector

    async def execute_operation(
        self,
        connector_id: str,
        operation_name: str,
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        provider: str | None = None,
    ) -> Any:
        # The connector lookup is pure existence-validation. When the caller has
        # already resolved the route -- the use-case resolve phase, which
        # validated the connector under a short DB scope -- skip this read so
        # the execute phase, which is the long external call, holds NO pooled
        # database connection.
        if provider is None:
            await self._get_connector(connector_id)
        timeout = connector_settings.connector_operation_timeout_seconds
        try:
            return await asyncio.wait_for(
                self._composio_gateway.execute_operation(
                    connector_id=connector_id,
                    operation_name=operation_name,
                    payload=payload,
                    third_party_credentials=third_party_credentials,
                ),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning(
                "connector.operation.timeout",
                operation_name=operation_name,
                connector_id=connector_id,
            )
            raise OperationExecutionTimeoutError(
                f"Operation '{operation_name}' timed out after {timeout:.0f}s. "
                "The upstream provider did not respond.",
                details={"connector_id": connector_id, "timeout_seconds": timeout},
            ) from exc
