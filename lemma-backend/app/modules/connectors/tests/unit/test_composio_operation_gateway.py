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


async def test_sdk_telemetry_is_opt_in_and_context_is_restored():
    from composio.core.models.base import allow_tracking

    seen_tracking_values: list[bool] = []
    composio = SimpleNamespace(
        tools=SimpleNamespace(
            execute=lambda *a, **k: (
                seen_tracking_values.append(allow_tracking.get())
                or {"successful": True, "data": {"ok": True}}
            )
        )
    )
    assert allow_tracking.get() is True

    result = await _run(
        ComposioOperationGateway(composio_client_factory=lambda: composio)
    )

    assert result == {"ok": True}
    assert seen_tracking_values == [False]
    assert allow_tracking.get() is True


class TestRaisedSdkFailuresAreClassified:
    """Composio reports some failures by raising, not by returning an envelope.

    A deleted or revoked connected account comes back as a raised 404. That used
    to fall through to "temporarily unavailable", so the account was never
    flagged for reauth: the user was never prompted to reconnect and the call
    failed forever looking like a transient outage.
    """

    @staticmethod
    def _classify(status_code, message="boom"):
        from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
            ComposioOperationGateway,
        )

        return ComposioOperationGateway._classify_failure(
            "GOOGLEDRIVE_LIST_FILES", status_code, message, {"provider": "composio"}
        )

    def test_a_missing_connected_account_is_not_found(self):
        from app.modules.connectors.domain.errors import (
            OperationExecutionNotFoundError,
        )

        assert isinstance(
            self._classify(404, "No connected account found with ID ca_x"),
            OperationExecutionNotFoundError,
        )

    def test_a_401_flags_the_account_for_reauth(self):
        from app.modules.connectors.domain.errors import (
            OperationExecutionUnauthorizedError,
        )

        # This specific mapping is what drives the reconnect prompt.
        assert isinstance(self._classify(401), OperationExecutionUnauthorizedError)

    def test_a_403_is_access_denied(self):
        from app.modules.connectors.domain.errors import (
            OperationExecutionAccessDeniedError,
        )

        assert isinstance(self._classify(403), OperationExecutionAccessDeniedError)

    @pytest.mark.parametrize("status", [400, 422])
    def test_bad_arguments_are_validation_errors(self, status):
        from app.modules.connectors.domain.errors import (
            OperationExecutionValidationError,
        )

        assert isinstance(self._classify(status), OperationExecutionValidationError)

    def test_an_unclassifiable_failure_stays_infrastructure(self):
        from app.modules.connectors.domain.errors import (
            OperationExecutionInfrastructureError,
        )

        assert isinstance(self._classify(None), OperationExecutionInfrastructureError)
        assert isinstance(self._classify(503), OperationExecutionInfrastructureError)

    def test_a_structured_token_still_classifies_without_a_status(self):
        from app.modules.connectors.domain.errors import (
            OperationExecutionUnauthorizedError,
        )

        # The returned-envelope form carries a token rather than an HTTP status.
        assert isinstance(
            self._classify(None, "unauthorized"), OperationExecutionUnauthorizedError
        )
