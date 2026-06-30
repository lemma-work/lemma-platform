from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    AuthProvider,
)
from app.modules.connectors.domain.errors import OperationExecutionTimeoutError
from app.modules.connectors.infrastructure.adapters.routing_operation_gateway import (
    RoutingOperationGateway,
)


@pytest.mark.asyncio
async def test_routes_lemma_apps_to_native_gateway():
    connector_repository = AsyncMock(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="google_calendar",
            )
        )
    )
    lemma_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"events": []}))
    composio_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"items": []}))

    gateway = RoutingOperationGateway(
        connector_repository=connector_repository,
        lemma_gateway=lemma_gateway,
        composio_gateway=composio_gateway,
    )

    result = await gateway.execute_operation(
        connector_id="google_calendar",
        operation_name="list_events",
        payload={"calendar_id": "primary"},
        third_party_credentials={"connection_id": "ca_123"},
    )

    assert result == {"events": []}
    lemma_gateway.execute_operation.assert_awaited_once()
    composio_gateway.execute_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_routes_composio_executor_apps_to_composio_gateway():
    connector_repository = AsyncMock(
        get=AsyncMock(
            return_value=ConnectorEntity(
                id="hubspot",
            )
        )
    )
    lemma_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"ignored": True}))
    composio_gateway = AsyncMock(
        execute_operation=AsyncMock(return_value={"records": []})
    )

    gateway = RoutingOperationGateway(
        connector_repository=connector_repository,
        lemma_gateway=lemma_gateway,
        composio_gateway=composio_gateway,
    )

    result = await gateway.execute_operation(
        connector_id="hubspot",
        operation_name="hubspot_list_contacts",
        payload={},
        third_party_credentials={"connection_id": "ca_123"},
        provider=AuthProvider.COMPOSIO.value,
    )

    assert result == {"records": []}
    composio_gateway.execute_operation.assert_awaited_once()
    lemma_gateway.execute_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_preresolved_provider_skips_connector_validation_read():
    # When the caller already resolved `provider` (the use-case resolve phase,
    # which validated the connector under a short DB scope), the gateway must NOT
    # touch the connector repository — so the external execute phase holds no DB
    # connection.
    connector_repository = AsyncMock(get=AsyncMock())
    lemma_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"ok": True}))

    gateway = RoutingOperationGateway(
        connector_repository=connector_repository,
        lemma_gateway=lemma_gateway,
        composio_gateway=AsyncMock(),
    )

    result = await gateway.execute_operation(
        connector_id="gmail",
        operation_name="drafts_list",
        payload={},
        third_party_credentials={"access_token": "t"},
        provider=AuthProvider.LEMMA.value,
    )

    assert result == {"ok": True}
    connector_repository.get.assert_not_awaited()
    lemma_gateway.execute_operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_provider_still_validates_connector():
    # Back-compat: callers that don't pre-resolve a provider keep the existence
    # validation (and its DB read).
    connector_repository = AsyncMock(
        get=AsyncMock(return_value=ConnectorEntity(id="gmail"))
    )
    lemma_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"ok": True}))

    gateway = RoutingOperationGateway(
        connector_repository=connector_repository,
        lemma_gateway=lemma_gateway,
        composio_gateway=AsyncMock(),
    )

    await gateway.execute_operation(
        connector_id="gmail",
        operation_name="drafts_list",
        payload={},
        third_party_credentials={"access_token": "t"},
    )

    connector_repository.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_operation_times_out_instead_of_hanging(monkeypatch):
    """A slow/hung upstream must fail fast with a 504, not block indefinitely."""
    monkeypatch.setattr(connector_settings, "connector_operation_timeout_seconds", 0.2)

    async def _hang(**_kwargs):
        await asyncio.sleep(5)
        return {"never": True}

    connector_repository = AsyncMock(
        get=AsyncMock(return_value=ConnectorEntity(id="gmail"))
    )
    lemma_gateway = AsyncMock(execute_operation=_hang)

    gateway = RoutingOperationGateway(
        connector_repository=connector_repository,
        lemma_gateway=lemma_gateway,
        composio_gateway=AsyncMock(),
    )

    with pytest.raises(OperationExecutionTimeoutError) as exc_info:
        await asyncio.wait_for(
            gateway.execute_operation(
                connector_id="gmail",
                operation_name="drafts_list",
                payload={},
                third_party_credentials={"access_token": "t"},
            ),
            timeout=2,  # outer guard: the gateway's own 0.2s bound must fire first
        )

    assert exc_info.value.status_code == 504
