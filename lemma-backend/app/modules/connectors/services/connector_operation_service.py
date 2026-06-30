from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDetail,
    OperationDetailsBatchResponse,
    OperationDiscoverResponse,
    OperationExecutionResponse,
    OperationSummary,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorNotFoundError,
    ConnectorDomainError,
    OperationExecutionInfrastructureError,
    OperationNotFoundError,
)
from app.modules.connectors.domain.ports import (
    ConnectorOperationRepositoryPort,
    ConnectorRepositoryPort,
    AppOperationGatewayPort,
    SchemaCompilerPort,
)
from app.modules.connectors.services.account_resolution_service import (
    AccountResolutionService,
)
from app.modules.connectors.services.connector_service import ConnectorService


@dataclass(frozen=True, slots=True)
class ResolvedConnectorExecution:
    """Session-free output of the connector-operation resolve phase, handed to
    the external execute phase so the operation runs with no pooled DB connection
    held. Carries only plain values -- never ORM/session-bound objects."""

    connector_id: str
    operation_execution_name: str
    provider: str | None
    third_party_credentials: dict[str, Any]
    payload: dict[str, Any]
    auth_token: str | None
    api_url: str | None
    # Plain identifiers (never session-bound) so the execute phase can flag the
    # account for re-auth on an unauthorized failure without re-resolving it.
    account_id: UUID | None = None
    account_user_id: UUID | None = None
    organization_id: UUID | None = None


class ConnectorOperationService:
    def __init__(
        self,
        *,
        connector_repository: ConnectorRepositoryPort,
        operation_repository: ConnectorOperationRepositoryPort,
        operation_gateway: AppOperationGatewayPort,
        schema_compiler: SchemaCompilerPort,
        account_resolution_service: AccountResolutionService,
        connector_service: ConnectorService | None = None,
    ):
        self.connector_repository = connector_repository
        self.operation_repository = operation_repository
        self.operation_gateway = operation_gateway
        self.schema_compiler = schema_compiler
        self.account_resolution_service = account_resolution_service
        self.connector_service = connector_service

    async def _get_connector(self, connector_id: str):
        connector = await self.connector_repository.get(connector_id)
        if not connector:
            raise ConnectorNotFoundError(connector_id)
        return connector

    async def _list_operation_entities(
        self,
        connector_id: str,
        *,
        provider: str | None = None,
        search_query: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        await self._get_connector(connector_id)
        if provider:
            operations = await self.operation_repository.list_by_connector_provider(
                connector_id,
                provider,
                search_query=search_query,
                limit=limit,
            )
        else:
            operations = await self.operation_repository.list_by_connector(
                connector_id,
                search_query=search_query,
                limit=limit,
            )
        return list(operations)

    async def _resolve_auth_config_context(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
    ):
        if self.connector_service is None:
            raise ConnectorNotFoundError(auth_config_name)
        auth_config = await self.connector_service.get_auth_config_by_name(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        provider = (
            auth_config.provider.value
            if hasattr(auth_config.provider, "value")
            else str(auth_config.provider)
        )
        return auth_config, auth_config.connector_id, provider

    def _normalize_operation_lookup_name(self, operation_name: str) -> str:
        return operation_name.strip().lower()

    def _operation_relevance_score(
        self,
        operation: Any,
        query: str | None,
    ) -> float | None:
        if not query:
            return None

        normalized_query = " ".join(
            query.replace("_", " ").replace("-", " ").replace("/", " ").lower().split()
        )
        if not normalized_query:
            return None

        tokens = normalized_query.split()
        name = str(getattr(operation, "name", "") or "").lower()
        provider_name = str(
            getattr(operation, "provider_operation_name", "") or ""
        ).lower()
        display_name = str(getattr(operation, "display_name", "") or "").lower()
        description = str(getattr(operation, "description", "") or "").lower()
        search_document = str(getattr(operation, "search_document", "") or "").lower()
        compact_names = {
            name,
            provider_name,
            display_name,
            name.replace("_", " "),
            provider_name.replace("_", " "),
        }
        name_text = " ".join(compact_names)
        all_text = " ".join([name_text, description, search_document])

        score = 0.0
        if normalized_query in compact_names:
            score = max(score, 1.0)
        if normalized_query and normalized_query in name_text:
            score = max(score, 0.95)
        if tokens:
            name_matches = sum(1 for token in tokens if token in name_text)
            all_matches = sum(1 for token in tokens if token in all_text)
            score = max(score, 0.85 * (name_matches / len(tokens)))
            score = max(score, 0.7 * (all_matches / len(tokens)))
        return round(score, 3)

    def _build_operation_summary(
        self,
        operation: Any,
        *,
        query: str | None = None,
    ) -> OperationSummary:
        return OperationSummary(
            name=operation.name,
            description=self._operation_summary_description(
                operation.name,
                operation.description,
            ),
            relevance_score=self._operation_relevance_score(operation, query),
        )

    def _build_operation_detail(self, operation: Any) -> OperationDetail:
        return OperationDetail(
            name=operation.name,
            description=self._operation_summary_description(
                operation.name,
                operation.description,
            ),
            input_schema=operation.input_schema or {},
            output_schema=operation.output_schema or {},
        )

    def _serialize_credentials(self, credentials: Any) -> dict[str, Any]:
        if credentials is None:
            raise AccountResolutionError("Resolved account has no credentials.")
        if isinstance(credentials, dict):
            return credentials
        model_dump = getattr(credentials, "model_dump", None)
        if callable(model_dump):
            return model_dump(exclude_none=True)
        raise AccountResolutionError(
            "Resolved account credentials are in unsupported format."
        )

    def _is_oauth_account(self, account: Any) -> bool:
        auth_method = getattr(
            getattr(account, "connector", None), "auth_method", None
        )
        if auth_method is not None and hasattr(auth_method, "value"):
            auth_method = auth_method.value
        if auth_method is not None:
            return str(auth_method).upper() == "OAUTH2"

        creds = getattr(account, "credentials", None)
        if isinstance(creds, dict):
            return any(
                key in creds
                for key in ("access_token", "refresh_token", "connection_id")
            )
        return any(
            hasattr(creds, key)
            for key in ("access_token", "refresh_token", "connection_id")
        )

    def _exception_details(self, exc: Exception) -> dict[str, Any]:
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            return details
        if details is not None:
            return {"upstream": details}
        return {"upstream_message": str(exc)}

    def _normalize_execution_result(self, value: Any) -> Any:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return self._normalize_execution_result(
                model_dump(by_alias=True, exclude_none=True, mode="json")
            )
        if isinstance(value, dict):
            return {
                key: self._normalize_execution_result(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._normalize_execution_result(item) for item in value]
        if isinstance(value, tuple):
            return [self._normalize_execution_result(item) for item in value]
        if isinstance(value, (bytes, bytearray)):
            return {
                "type": "binary_content",
                "content_base64": base64.b64encode(bytes(value)).decode("ascii"),
                "media_type": "application/octet-stream",
                "size_bytes": len(value),
            }
        return value

    async def _resolve_execution_credentials(
        self, account: Any, user_id: UUID
    ) -> dict[str, Any]:
        stored_credentials = self._serialize_credentials(account.credentials)
        if not self.connector_service or not self._is_oauth_account(account):
            return stored_credentials

        refreshed = await self.connector_service.get_account_credentials(
            account.id,
            user_id,
            account.organization_id,
            force_refresh=True,
        )
        refreshed_credentials = self._serialize_credentials(refreshed)
        if "user_data" not in refreshed_credentials and stored_credentials.get(
            "user_data"
        ):
            refreshed_credentials["user_data"] = stored_credentials["user_data"]
        return refreshed_credentials

    def _compact_description(
        self, description: str | None, *, max_length: int = 120
    ) -> str:
        if not description:
            return "No description available."
        compact = " ".join(description.split())
        if len(compact) <= max_length:
            return compact
        return f"{compact[: max_length - 3].rstrip()}..."

    def _operation_summary_description(
        self,
        operation_name: str,
        description: str | None,
    ) -> str:
        if description:
            return self._compact_description(description)
        return operation_name.replace("_", " ").strip().capitalize()

    async def list_operations(
        self,
        connector_id: str,
        search_query: str | None = None,
        limit: int | None = None,
    ) -> list[OperationSummary]:
        operations = await self._list_operation_entities(
            connector_id,
            search_query=search_query,
            limit=limit,
        )
        return [self._build_operation_summary(operation) for operation in operations]

    async def discover_operations_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        query: str | None = None,
        limit: int | None = None,
    ) -> OperationDiscoverResponse:
        _auth_config, connector_id, provider = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.discover_operations(
            connector_id,
            query=query,
            limit=limit,
            provider=provider,
        )

    async def discover_operations(
        self,
        connector_id: str,
        query: str | None = None,
        limit: int | None = None,
        provider: str | None = None,
    ) -> OperationDiscoverResponse:
        total_operations = len(
            await self._list_operation_entities(connector_id, provider=provider)
        )
        selected_operations = await self._list_operation_entities(
            connector_id,
            provider=provider,
            search_query=query,
            limit=limit,
        )

        items = [
            self._build_operation_summary(operation, query=query)
            for operation in selected_operations
        ]
        return OperationDiscoverResponse(
            connector_id=connector_id,
            query=query,
            items=items,
            total_operations=total_operations,
            returned_count=len(items),
        )

    async def get_operation_details(
        self,
        connector_id: str,
        operation_name: str,
        provider: str | None = None,
    ) -> OperationDetail:
        await self._get_connector(connector_id)
        if provider:
            operation = (
                await self.operation_repository.get_by_connector_provider_and_name(
                    connector_id,
                    provider,
                    operation_name,
                )
            )
        else:
            operation = await self.operation_repository.get_by_connector_and_name(
                connector_id,
                operation_name,
            )
        if not operation:
            raise OperationNotFoundError(operation_name)
        return self._build_operation_detail(operation)

    async def get_operation_details_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        operation_name: str,
    ) -> OperationDetail:
        _auth_config, connector_id, provider = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.get_operation_details(
            connector_id,
            operation_name,
            provider=provider,
        )

    async def get_operation_details_batch(
        self,
        connector_id: str,
        operation_names: list[str] | None = None,
        provider: str | None = None,
    ) -> OperationDetailsBatchResponse:
        operations = await self._list_operation_entities(
            connector_id,
            provider=provider,
        )
        operations_by_name = {
            self._normalize_operation_lookup_name(operation.name): operation
            for operation in operations
        }
        operations_by_provider_name = {
            self._normalize_operation_lookup_name(operation.provider_operation_name): operation
            for operation in operations
            if operation.provider_operation_name
        }

        if operation_names:
            selected_operations: list[Any] = []
            for operation_name in operation_names:
                normalized_name = self._normalize_operation_lookup_name(operation_name)
                operation = operations_by_name.get(
                    normalized_name
                ) or operations_by_provider_name.get(normalized_name)
                if not operation:
                    raise OperationNotFoundError(operation_name)
                selected_operations.append(operation)
        else:
            selected_operations = operations

        items = [
            self._build_operation_detail(operation) for operation in selected_operations
        ]
        return OperationDetailsBatchResponse(
            connector_id=connector_id,
            items=items,
            returned_count=len(items),
        )

    async def get_operation_details_batch_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        operation_names: list[str] | None = None,
    ) -> OperationDetailsBatchResponse:
        _auth_config, connector_id, provider = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.get_operation_details_batch(
            connector_id,
            operation_names=operation_names,
            provider=provider,
        )

    # -- Resolve / execute split ------------------------------------------------
    # ``resolve_execution*`` does all the DB reads + authorization + credential
    # resolution and returns a session-free ``ResolvedConnectorExecution``;
    # ``execute_resolved`` performs only the external gateway call. The
    # ConnectorOperationUseCases runs resolve in a short DB scope and execute with
    # no connection held. ``execute_operation*`` remain as thin wrappers
    # (resolve + execute) for any single-shot internal callers.

    async def resolve_execution_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        operation_name: str,
        payload: dict[str, Any],
        actor: Context | None = None,
        auth_token: str | None = None,
        api_url: str | None = None,
        account_id: UUID | None = None,
    ) -> ResolvedConnectorExecution:
        auth_config, connector_id, _provider = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self._resolve_execution(
            connector_id=connector_id,
            operation_name=operation_name,
            payload=payload,
            user_id=user_id,
            actor=actor,
            auth_token=auth_token,
            api_url=api_url,
            account_id=account_id,
            auth_config_id=auth_config.id,
        )

    async def _resolve_execution(
        self,
        *,
        connector_id: str,
        operation_name: str,
        payload: dict[str, Any],
        user_id: UUID,
        actor: Context | None = None,
        auth_token: str | None = None,
        api_url: str | None = None,
        account_id: UUID | None = None,
        auth_config_id: UUID | None = None,
    ) -> ResolvedConnectorExecution:
        await self._get_connector(connector_id)
        provider: str | None = None
        if auth_config_id is not None:
            if self.connector_service is None:
                raise ConnectorNotFoundError(connector_id)
            auth_config = await self.connector_service.auth_config_repository.get(
                auth_config_id
            )
            if auth_config is None:
                raise ConnectorNotFoundError(str(auth_config_id))
            provider = (
                auth_config.provider.value
                if hasattr(auth_config.provider, "value")
                else str(auth_config.provider)
            )
            account = await self.account_resolution_service.resolve_account_for_auth_config(
                user_id=user_id,
                connector_id=connector_id,
                auth_config_id=auth_config_id,
                auth_actor=actor,
                account_id=account_id,
            )
        else:
            account = await self.account_resolution_service.resolve_account(
                user_id=user_id,
                connector_id=connector_id,
                auth_actor=actor,
                account_id=account_id,
            )
            if self.connector_service is not None:
                auth_config = await self.connector_service.auth_config_repository.get(
                    account.auth_config_id
                )
                if auth_config is not None:
                    provider = (
                        auth_config.provider.value
                        if hasattr(auth_config.provider, "value")
                        else str(auth_config.provider)
                    )

        if provider:
            operation = await self.operation_repository.get_by_connector_provider_and_name(
                connector_id,
                provider,
                operation_name,
            )
        else:
            operation = await self.operation_repository.get_by_connector_and_name(
                connector_id,
                operation_name,
            )
        if not operation:
            raise OperationNotFoundError(operation_name)

        third_party_credentials = await self._resolve_execution_credentials(
            account,
            user_id,
        )
        return ResolvedConnectorExecution(
            connector_id=connector_id,
            operation_execution_name=operation.execution_name,
            # Resolve the provider to a concrete value (the gateway already maps
            # None -> LEMMA for routing). This guarantees the execute phase passes
            # a non-None provider, so the gateway skips its connector-validation
            # read and the external call holds NO DB connection.
            provider=provider or AuthProvider.LEMMA.value,
            third_party_credentials=third_party_credentials,
            payload=payload or {},
            auth_token=auth_token,
            api_url=api_url,
            account_id=getattr(account, "id", None),
            account_user_id=getattr(account, "user_id", None),
            organization_id=getattr(account, "organization_id", None),
        )

    async def execute_resolved(
        self, resolved: ResolvedConnectorExecution
    ) -> OperationExecutionResponse:
        """Run the external operation for an already-resolved plan. Holds NO DB
        connection: the gateway's connector-validation read is skipped because
        ``provider`` is supplied (the connector was validated in the resolve
        phase), and the concrete provider gateways are DB-free."""
        try:
            result = await self.operation_gateway.execute_operation(
                connector_id=resolved.connector_id,
                operation_name=resolved.operation_execution_name,
                payload=resolved.payload or {},
                third_party_credentials=resolved.third_party_credentials,
                auth_token=resolved.auth_token,
                api_url=resolved.api_url,
                provider=resolved.provider,
            )
        except ConnectorDomainError:
            raise
        except Exception as exc:
            raise OperationExecutionInfrastructureError(
                f"Failed to execute '{resolved.operation_execution_name}' for "
                f"'{resolved.connector_id}'.",
                details=self._exception_details(exc),
            ) from exc
        return OperationExecutionResponse(
            result=self._normalize_execution_result(result)
        )

    async def execute_operation_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        operation_name: str,
        payload: dict[str, Any],
        actor: Context | None = None,
        auth_token: str | None = None,
        api_url: str | None = None,
        account_id: UUID | None = None,
    ) -> OperationExecutionResponse:
        resolved = await self.resolve_execution_for_auth_config(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
            operation_name=operation_name,
            payload=payload,
            actor=actor,
            auth_token=auth_token,
            api_url=api_url,
            account_id=account_id,
        )
        return await self.execute_resolved(resolved)

    async def execute_operation(
        self,
        *,
        connector_id: str,
        operation_name: str,
        payload: dict[str, Any],
        user_id: UUID,
        actor: Context | None = None,
        auth_token: str | None = None,
        api_url: str | None = None,
        account_id: UUID | None = None,
        auth_config_id: UUID | None = None,
    ) -> OperationExecutionResponse:
        resolved = await self._resolve_execution(
            connector_id=connector_id,
            operation_name=operation_name,
            payload=payload,
            user_id=user_id,
            actor=actor,
            auth_token=auth_token,
            api_url=api_url,
            account_id=account_id,
            auth_config_id=auth_config_id,
        )
        return await self.execute_resolved(resolved)
