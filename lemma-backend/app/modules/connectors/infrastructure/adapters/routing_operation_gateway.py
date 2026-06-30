from __future__ import annotations

import asyncio
from typing import Any

from app.modules.connectors.config import connector_settings
from app.core.log.log import get_logger
from app.modules.connectors.domain.connector import ConnectorEntity, AuthProvider
from app.modules.connectors.domain.errors import (
    ConnectorNotFoundError,
    OperationExecutionTimeoutError,
)
from app.modules.connectors.domain.ports import (
    AppOperationGatewayPort,
    ConnectorRepositoryPort,
    OperationDetailsPort,
)

logger = get_logger(__name__)
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)
from app.modules.connectors.infrastructure.adapters.lemma_operation_gateway import (
    LemmaOperationGateway,
)


class RoutingOperationGateway(AppOperationGatewayPort):
    def __init__(
        self,
        *,
        connector_repository: ConnectorRepositoryPort,
        lemma_gateway: LemmaOperationGateway | None = None,
        composio_gateway: ComposioOperationGateway | None = None,
    ):
        self._connector_repository = connector_repository
        self._lemma_gateway = lemma_gateway or LemmaOperationGateway()
        self._composio_gateway = composio_gateway or ComposioOperationGateway()

    async def _get_connector(self, connector_id: str) -> ConnectorEntity:
        connector = await self._connector_repository.get(connector_id)
        if not connector:
            raise ConnectorNotFoundError(connector_id)
        return connector

    def _get_gateway_by_provider(
        self, provider: str
    ) -> AppOperationGatewayPort:
        if provider.upper() == AuthProvider.COMPOSIO.value:
            return self._composio_gateway
        if provider.upper() == AuthProvider.LEMMA.value:
            return self._lemma_gateway
        raise ValueError(f"Unsupported operation provider: {provider}")

    async def list_operations(self, connector_id: str) -> list[str]:
        await self._get_connector(connector_id)
        gateway = self._lemma_gateway
        return list(await gateway.list_operations(connector_id))

    async def get_operation_details(
        self, connector_id: str, operation_name: str
    ) -> OperationDetailsPort:
        await self._get_connector(connector_id)
        gateway = self._lemma_gateway
        return await gateway.get_operation_details(connector_id, operation_name)

    async def execute_operation(
        self,
        connector_id: str,
        operation_name: str,
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        auth_token: str | None = None,
        api_url: str | None = None,
        provider: str | None = None,
    ) -> Any:
        # The connector lookup is pure existence-validation (the entity is
        # discarded; routing uses ``provider``). When the caller already resolved
        # ``provider`` -- the use-case resolve phase, which validated the
        # connector under a short DB scope -- skip this read so the execute phase
        # (this long external call) holds NO pooled DB connection. Concrete
        # Lemma/Composio gateways are DB-free. When ``provider`` is unknown we
        # still validate + derive the default route as before.
        if provider is None:
            await self._get_connector(connector_id)
        gateway = self._get_gateway_by_provider(provider or AuthProvider.LEMMA.value)
        # Hard-bound the upstream call. The LEMMA path is async (httpx.AsyncClient)
        # and the COMPOSIO path is offloaded via asyncio.to_thread, so neither
        # blocks the loop — but without a timeout a client-abandoned request runs
        # forever, holding a DB connection (and a thread slot for Composio) until
        # the pools exhaust and the whole backend wedges. wait_for cancels the
        # async LEMMA call cleanly; for the Composio thread it lets the request
        # fail fast and release its connection even if the SDK call lingers.
        timeout = connector_settings.connector_operation_timeout_seconds
        try:
            return await asyncio.wait_for(
                gateway.execute_operation(
                    connector_id=connector_id,
                    operation_name=operation_name,
                    payload=payload,
                    third_party_credentials=third_party_credentials,
                    auth_token=auth_token,
                    api_url=api_url,
                ),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning(
                "connector operation %s on %s timed out after %.0fs",
                operation_name,
                connector_id,
                timeout,
            )
            raise OperationExecutionTimeoutError(
                f"Operation '{operation_name}' timed out after {timeout:.0f}s. "
                "The upstream provider did not respond.",
                details={"connector_id": connector_id, "timeout_seconds": timeout},
            ) from exc
