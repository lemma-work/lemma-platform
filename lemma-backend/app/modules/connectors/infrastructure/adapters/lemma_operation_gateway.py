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
from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutor,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.infrastructure.adapters.sql_executor import SqlExecutor
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Process-shared executors for kinds that hold reusable resources (SQL engine
# pools). The gateway is constructed per-request, so a fresh executor per request
# would defeat connection pooling — share one across the process instead.
_SHARED_SQL_EXECUTOR: SqlExecutor | None = None


def _shared_sql_executor() -> SqlExecutor:
    global _SHARED_SQL_EXECUTOR
    if _SHARED_SQL_EXECUTOR is None:
        _SHARED_SQL_EXECUTOR = SqlExecutor()
    return _SHARED_SQL_EXECUTOR


@dataclass(slots=True)
class LemmaOperationDetails(OperationDetailsPort):
    description: str | None = None
    input_schema_content: str | None = None
    output_schema_content: str | None = None


class LemmaOperationGateway(AppOperationGatewayPort):
    def __init__(
        self,
        http_executor: OpenApiHttpExecutor | None = None,
        sql_executor: SqlExecutor | None = None,
        mcp_executor: McpExecutor | None = None,
    ):
        # Operations carrying an ``execution`` descriptor run package-free through
        # a per-kind executor (http/sql/mcp); everything else keeps using the
        # vendored ``lemma-connectors`` package path.
        self._http_executor = http_executor or OpenApiHttpExecutor()
        self._sql_executor = sql_executor or _shared_sql_executor()
        self._mcp_executor = mcp_executor or McpExecutor()

    async def _execute_by_kind(
        self,
        *,
        connector_id: str,
        operation_name: str,
        execution: dict[str, Any],
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        connection_config: dict[str, Any] | None = None,
    ) -> Any:
        kind = (execution.get("kind") or "http").lower()
        if kind == "http":
            return await self._http_executor.execute(
                connector_id=connector_id,
                operation_name=operation_name,
                execution=execution,
                payload=payload,
                third_party_credentials=third_party_credentials,
                connection_config=connection_config,
            )
        if kind == "sql":
            return await self._sql_executor.execute(
                connector_id=connector_id,
                operation_name=operation_name,
                execution=execution,
                payload=payload,
                third_party_credentials=third_party_credentials,
                connection_config=connection_config,
            )
        if kind == "mcp":
            return await self._mcp_executor.execute(
                connector_id=connector_id,
                operation_name=operation_name,
                execution=execution,
                payload=payload,
                third_party_credentials=third_party_credentials,
                connection_config=connection_config,
            )
        raise OperationExecutionValidationError(
            f"Unsupported execution kind '{kind}' for operation '{operation_name}'.",
            details={"kind": kind},
        )

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
        execution: dict[str, Any] | None = None,
        connection_config: dict[str, Any] | None = None,
    ) -> Any:
        del auth_token, api_url, provider
        logger.debug(
            "connectors.lemma_operation_gateway.calling_s_native_operation_s.observed",
            connector_id=connector_id,
            operation_name=operation_name,
        )
        try:
            if execution:
                # Package-free operation: dispatch by execution kind. Each kind's
                # executor is self-contained (HTTP / SQL / MCP); the vendored
                # package path below is only for operations without a descriptor.
                return await self._execute_by_kind(
                    connector_id=connector_id,
                    operation_name=operation_name,
                    execution=execution,
                    payload=payload or {},
                    third_party_credentials=third_party_credentials,
                    connection_config=connection_config,
                )
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
