from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)

pytestmark = pytest.mark.asyncio


def _gateway(execute_response):
    composio = SimpleNamespace(
        tools=SimpleNamespace(execute=lambda *a, **k: execute_response)
    )
    return ComposioOperationGateway(composio_client_factory=lambda: composio)


async def _run(gateway):
    return await gateway.execute_operation(
        connector_id="openweather_api",
        operation_name="OPENWEATHER_API_GET_CURRENT_WEATHER",
        payload={"q": "London"},
        third_party_credentials={"connection_id": "ca_test"},
        provider="COMPOSIO",
    )


async def test_provider_passthrough_401_maps_to_unauthorized():
    # Mirrors a real OpenWeather bad-key failure surfaced by Composio: free-text
    # error, HTTP status nested in `data.status_code`.
    response = {
        "successful": False,
        "error": "Error fetching current weather: HTTP 401. Invalid API key.",
        "data": {"status_code": 401, "message": "Invalid API key."},
    }
    with pytest.raises(OperationExecutionUnauthorizedError):
        await _run(_gateway(response))


async def test_provider_passthrough_403_maps_to_access_denied():
    response = {
        "successful": False,
        "error": "Forbidden",
        "data": {"status_code": 403},
    }
    with pytest.raises(OperationExecutionAccessDeniedError):
        await _run(_gateway(response))


async def test_structured_unauthorized_token_still_maps():
    response = {"successful": False, "error": "unauthorized"}
    with pytest.raises(OperationExecutionUnauthorizedError):
        await _run(_gateway(response))


async def test_unclassified_error_remains_infrastructure():
    response = {
        "successful": False,
        "error": "upstream exploded",
        "data": {"status_code": 502},
    }
    with pytest.raises(OperationExecutionInfrastructureError):
        await _run(_gateway(response))


async def test_successful_response_returns_data():
    response = {"successful": True, "data": {"name": "London"}}
    result = await _run(_gateway(response))
    assert result == {"name": "London"}
