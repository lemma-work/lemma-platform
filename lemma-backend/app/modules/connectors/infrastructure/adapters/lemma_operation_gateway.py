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
        # Exception text is useful for local classification but may contain
        # provider request bodies, callback URLs, or credentials. Never attach
        # it to a domain error or log record.
        normalized_error = str(exc).lower()
        payload: dict[str, object] = {"error_type": type(exc).__name__}
        if isinstance(status_code, int):
            payload["upstream_status"] = status_code
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
        auth_token: str | None = None,
        api_url: str | None = None,
        provider: str | None = None,
    ) -> Any:
        del auth_token, api_url, provider
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
