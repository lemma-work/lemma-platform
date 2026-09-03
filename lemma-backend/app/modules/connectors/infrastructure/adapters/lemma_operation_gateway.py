from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationNotFoundError,
    OperationExecutionNotFoundError,
    OperationExecutionUnauthorizedError,
    OperationExecutionValidationError,
)
from app.modules.connectors.domain.ports import (
    AppOperationGatewayPort,
    OperationDetailsPort,
)
from app.modules.connectors.infrastructure.adapters.lemma_connector_factory import (
    create_lemma_execution_client,
    create_lemma_info_client,
    schema_json,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class LemmaOperationDetails(OperationDetailsPort):
    description: str | None = None
    input_schema_content: str | None = None
    output_schema_content: str | None = None


class LemmaOperationGateway(AppOperationGatewayPort):
    def _translate_execution_error(
        self,
        operation_name: str,
        connector_id: str,
        exc: Exception,
    ) -> Exception:
        details = getattr(exc, "details", None)
        status_code = getattr(exc, "status_code", None)
        normalized_error = str(exc).lower()
        payload: dict[str, object] = {"error_type": type(exc).__name__}
        if isinstance(status_code, int):
            payload["upstream_status"] = status_code
        provider_said = self._provider_message(exc)
        if provider_said:
            payload["upstream_message"] = provider_said
        if isinstance(details, dict):
            error_value = details.get("error")
            if isinstance(error_value, str):
                normalized_error = error_value.lower()
                if len(error_value) <= 100:
                    payload["upstream_code"] = error_value

        message = f"Connector operation '{operation_name}' failed."
        if status_code == 400 or any(
            token in normalized_error
            for token in ("bad_request", "invalid", "validation")
        ):
            return OperationExecutionValidationError(message, details=payload)
        if status_code == 401 or any(
            token in normalized_error for token in ("unauthorized", "not_authed")
        ):
            return OperationExecutionUnauthorizedError(message, details=payload)
        if status_code == 403 or any(
            token in normalized_error for token in ("forbidden", "missing_scope")
        ):
            return OperationExecutionAccessDeniedError(message, details=payload)
        if status_code == 404 or "not_found" in normalized_error:
            return OperationExecutionNotFoundError(message, details=payload)
        return OperationExecutionInfrastructureError(message, details=payload)

    @staticmethod
    def _provider_message(exc: Exception) -> str | None:
        """What Gmail, Slack or Jira actually said, when the client relayed it.

        The http/sql/mcp kinds have always passed this through, and
        `_safe_connector_details` allowlists `upstream_message` precisely so it
        reaches the caller. This gateway dropped it, so the connectors most
        people use on day one were the ones that could not tell "invalid_scope"
        from "message not found" -- for a person reading the failure, or for an
        agent trying to correct itself.

        Narrowed to the vendored clients' own exception type, because that is
        the only text known to be a relay rather than an internal. Those
        clients build it from the provider's status and response body
        (`_raise_for_status`) or wrap the transport error verbatim, and unlike
        our own executors they attach no status code -- which is why the
        shared `upstream_message` heuristic cannot recognise them. Everything
        else that reaches here is ours, and its text stays here.
        """
        from lemma_connectors.core.errors import IntegrationError

        from app.modules.connectors.services.execution.failure_translation import (
            redacted_upstream_text,
        )

        if not isinstance(exc, IntegrationError):
            return None
        return redacted_upstream_text(str(exc))

    async def list_operations(self, connector_id: str) -> list[str]:
        info_client = create_lemma_info_client(connector_id)
        return [descriptor.name for descriptor in await info_client.list_operations()]

    async def get_operation_details(
        self, connector_id: str, operation_name: str
    ) -> OperationDetailsPort:
        info_client = create_lemma_info_client(connector_id)
        operation = await info_client.get_operation(operation_name)
        descriptor = operation.descriptor
        return LemmaOperationDetails(
            description=descriptor.description,
            input_schema_content=schema_json(descriptor.input_schema()),
            output_schema_content=schema_json(descriptor.output_schema()),
        )

    def _prepare_payload(
        self,
        operation: Any,
        operation_name: str,
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
    ) -> dict[str, Any]:
        prepared = dict(payload or {})
        descriptor = getattr(operation, "descriptor", None)
        input_model = getattr(descriptor, "input_model", None)
        fields = getattr(input_model, "model_fields", None)
        if not isinstance(fields, dict):
            logger.debug(
                "connectors.lemma_operation_gateway.skipping_token_autofill_s_because.observed",
                operation_name=operation_name,
            )
            return prepared
        access_token = (
            third_party_credentials.get("access_token")
            if isinstance(third_party_credentials, dict)
            else None
        )
        if "token" in fields and access_token and "token" not in prepared:
            prepared["token"] = access_token
        return prepared

    async def execute_operation(
        self,
        connector_id: str,
        operation_name: str,
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        provider: str | None = None,
    ) -> Any:
        del provider
        logger.debug(
            "connectors.lemma_operation_gateway.calling_s_native_operation_s.observed",
            connector_id=connector_id,
            operation_name=operation_name,
        )
        try:
            client = create_lemma_execution_client(
                connector_id, third_party_credentials
            )
            operation = await client.get_operation(operation_name)
            prepared_payload = self._prepare_payload(
                operation, operation_name, payload, third_party_credentials
            )
            return await client.execute_operation(operation_name, prepared_payload)
        except OperationNotFoundError:
            raise
        except Exception as exc:
            raise self._translate_execution_error(
                operation_name,
                connector_id,
                exc,
            ) from exc
