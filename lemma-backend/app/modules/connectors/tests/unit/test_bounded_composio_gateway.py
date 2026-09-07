"""The gateway that bounds the Composio call.

It used to route between two gateways on the legacy provider vocabulary. The
vendored connector clients are gone and every other kind reaches its executor
through the kind registry, so the routing cases went with them and what is left
is the timeout and the connector-existence read -- both of which are the reason
this wrapper exists at all.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.connector import AuthProvider, ConnectorEntity
from app.modules.connectors.domain.errors import OperationExecutionTimeoutError
from app.modules.connectors.infrastructure.adapters.bounded_composio_gateway import (
    BoundedComposioGateway,
)


@pytest.mark.asyncio
async def test_an_operation_reaches_the_composio_gateway():
    connector_repository = AsyncMock(
        get=AsyncMock(return_value=ConnectorEntity(id="hubspot"))
    )
    composio_gateway = AsyncMock(
        execute_operation=AsyncMock(return_value={"records": []})
    )

    gateway = BoundedComposioGateway(
        connector_repository=connector_repository,
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


@pytest.mark.asyncio
async def test_preresolved_provider_skips_connector_validation_read():
    # When the caller already resolved `provider` (the use-case resolve phase,
    # which validated the connector under a short DB scope), the gateway must NOT
    # touch the connector repository — so the external execute phase holds no DB
    # connection.
    connector_repository = AsyncMock(get=AsyncMock())
    composio_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"ok": True}))

    gateway = BoundedComposioGateway(
        connector_repository=connector_repository,
        composio_gateway=composio_gateway,
    )

    result = await gateway.execute_operation(
        connector_id="hubspot",
        operation_name="hubspot_list_contacts",
        payload={},
        third_party_credentials={"connection_id": "ca_123"},
        provider=AuthProvider.COMPOSIO.value,
    )

    assert result == {"ok": True}
    connector_repository.get.assert_not_awaited()
    composio_gateway.execute_operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_provider_still_validates_connector():
    # Callers that don't pre-resolve a provider keep the existence validation
    # (and its DB read).
    connector_repository = AsyncMock(
        get=AsyncMock(return_value=ConnectorEntity(id="hubspot"))
    )
    composio_gateway = AsyncMock(execute_operation=AsyncMock(return_value={"ok": True}))

    gateway = BoundedComposioGateway(
        connector_repository=connector_repository,
        composio_gateway=composio_gateway,
    )

    await gateway.execute_operation(
        connector_id="hubspot",
        operation_name="hubspot_list_contacts",
        payload={},
        third_party_credentials={"connection_id": "ca_123"},
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
        get=AsyncMock(return_value=ConnectorEntity(id="hubspot"))
    )

    gateway = BoundedComposioGateway(
        connector_repository=connector_repository,
        composio_gateway=AsyncMock(execute_operation=_hang),
    )

    with pytest.raises(OperationExecutionTimeoutError) as exc_info:
        await asyncio.wait_for(
            gateway.execute_operation(
                connector_id="hubspot",
                operation_name="hubspot_list_contacts",
                payload={},
                third_party_credentials={"connection_id": "ca_123"},
            ),
            timeout=2,  # outer guard: the gateway's own 0.2s bound must fire first
        )

    assert exc_info.value.status_code == 504
