from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.core.concurrency.offload import run_blocking
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import (
    ConnectorValidationError,
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
    OperationExecutionTimeoutError,
    OperationExecutionUnauthorizedError,
    OperationExecutionValidationError,
)
from app.modules.connectors.domain.ports import (
    AppOperationGatewayPort,
    OperationDetailsPort,
)


ComposioClientFactory = Callable[[], Any]


class _UnsupportedComposioDetails(OperationDetailsPort):
    description: str | None = None
    input_schema_content: str | None = None
    output_schema_content: str | None = None


class ComposioOperationGateway(AppOperationGatewayPort):
    def __init__(
        self,
        composio_client_factory: ComposioClientFactory | None = None,
    ):
        self._composio_client_factory = composio_client_factory or self._default_client_factory

    def _default_client_factory(self) -> Any:
        # Shared, not built here: this gateway is constructed per request and
        # the SDK client costs 42-262ms to build. See
        # `connectors.infrastructure.composio_client` for the flag's meaning.
        from app.modules.connectors.infrastructure.composio_client import (
            get_composio_client,
        )

        return get_composio_client(
            allow_managed_files=(
                connector_settings.connector_composio_managed_files_enabled
            )
        )

    async def list_operations(self, connector_id: str) -> list[str]:
        raise ConnectorValidationError(
            "Operation discovery is served from the connector catalog."
        )

    async def get_operation_details(
        self, connector_id: str, operation_name: str
    ) -> OperationDetailsPort:
        return _UnsupportedComposioDetails()

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
        del connector_id, auth_token, api_url, provider
        connection_id = (
            third_party_credentials.get("connection_id")
            if isinstance(third_party_credentials, dict)
            else None
        )
        if not connection_id:
            raise OperationExecutionValidationError(
                "Composio execution requires a connected account id.",
                details={"provider": "composio"},
            )

        def _execute() -> Any:
            # The SDK enables its own external telemetry by default. Keep that
            # opt-in at the application boundary so connector execution
            # metadata and provider failures are not exported unexpectedly.
            from composio.core.models.base import allow_tracking

            tracking_token = allow_tracking.set(
                connector_settings.composio_sdk_telemetry_enabled
            )
            try:
                composio = self._composio_client_factory()
                response = composio.tools.execute(
                    operation_name,
                    payload or {},
                    connected_account_id=connection_id,
                    dangerously_skip_version_check=True,
                )
                if hasattr(response, "model_dump"):
                    return response.model_dump()
                return response
            finally:
                allow_tracking.reset(tracking_token)

        try:
            # Composio's SDK is synchronous HTTP; run it on the dedicated
            # external-HTTP limiter so a burst of connector calls can't drain the
            # shared thread pool and stall unrelated (CPU) offloads.
            #
            # A backstop, and only that. Callers arriving through
            # `RoutingOperationGateway` are already wrapped in a `wait_for` at
            # `connector_operation_timeout_seconds` (45s), and the kind
            # dispatcher has its own Composio ceiling of 90s — both fire long
            # before this does, so their behaviour is unchanged.
            #
            # What this covers is the caller that does not go through either:
            # `agent_surfaces/platforms/composio_email.py` constructs this
            # gateway and calls `execute_operation` directly, and that path was
            # unbounded. An unresponsive provider there holds a thread from the
            # bounded external-HTTP pool for as long as the socket stays open,
            # and enough of them empties the pool for every other connector.
            response = await asyncio.wait_for(
                run_blocking(_execute, limiter="external_http"),
                timeout=connector_settings.connector_composio_deadline_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise OperationExecutionTimeoutError(
                f"Composio tool execution for '{operation_name}' exceeded "
                f"{connector_settings.connector_composio_deadline_seconds:.0f}s.",
                details={"provider": "composio", "operation": operation_name},
            ) from exc
        except Exception as exc:
            # The SDK reports some failures by *raising* rather than returning a
            # failure envelope -- a deleted or revoked connected account comes
            # back as a raised 404. Classifying only the returned form left those
            # as "temporarily unavailable", so the account was never flagged for
            # reauth and the user was never prompted to reconnect: it simply
            # failed forever, looking like a transient outage.
            status_code = getattr(exc, "status_code", None)
            raise self._classify_failure(
                operation_name,
                status_code if isinstance(status_code, int) else None,
                str(exc),
                {"provider": "composio", "upstream_message": str(exc)},
            ) from exc
        if not isinstance(response, dict):
            return response

        if not response.get("successful", False):
            error = response.get("error") or "Unknown Composio execution error"
            raise self._classify_failure(
                operation_name,
                self._error_status_code(response),
                str(error),
                {"provider": "composio", "error": error, "response": response},
            )
        return response.get("data")

    @staticmethod
    def _classify_failure(
        operation_name: str,
        status_code: int | None,
        error: str,
        details: dict[str, Any],
    ) -> Exception:
        """Map a Composio failure onto the domain error it deserves.

        Composio reports failures three ways: a structured token ("unauthorized"),
        a provider passthrough whose status is buried in the response data
        (OpenWeather's "HTTP 401"), or a raised SDK exception carrying an HTTP
        status. All three land here, because the classification decides real
        behaviour -- an Unauthorized flags the account for reauth so the user is
        prompted to reconnect, while an Infrastructure error just retries
        forever against a connection that is never coming back.
        """
        message = f"Composio tool execution failed for '{operation_name}': {error}"
        normalized = error.lower()

        def matches(tokens: set[str], *statuses: int) -> bool:
            return normalized in tokens or (
                status_code is not None and status_code in statuses
            )

        if matches({"not_found", "tool_not_found"}, 404) or "not found" in normalized:
            return OperationExecutionNotFoundError(message, details=details)
        if matches({"unauthorized", "not_authed", "invalid_auth"}, 401):
            return OperationExecutionUnauthorizedError(message, details=details)
        if matches({"forbidden", "missing_scope"}, 403):
            return OperationExecutionAccessDeniedError(message, details=details)
        if matches({"invalid_arguments", "validation_error", "bad_request"}, 400, 422):
            return OperationExecutionValidationError(message, details=details)
        return OperationExecutionInfrastructureError(message, details=details)

    @staticmethod
    def _error_status_code(response: dict[str, Any]) -> int | None:
        """Best-effort HTTP status from a failed Composio tool response."""
        candidates: list[Any] = [response.get("status_code")]
        data = response.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("status_code"))
        for candidate in candidates:
            if isinstance(candidate, bool):
                continue
            if isinstance(candidate, int):
                return candidate
            if isinstance(candidate, str) and candidate.isdigit():
                return int(candidate)
        return None
