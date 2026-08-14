from __future__ import annotations

import base64
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
from app.modules.connectors.domain.connector import (
    AuthProvider,
    ConnectorKind,
    kind_to_provider,
)
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorNotFoundError,
    OperationNotFoundError,
)
from app.modules.connectors.domain.ports import (
    ConnectorOperationRepositoryPort,
    ConnectorRepositoryPort,
    AppOperationGatewayPort,
)
from app.modules.connectors.services.operation_visibility import (
    find_install_or_catalog_operation,
    list_operations_for_install,
)
from app.modules.connectors.services.account_resolution_service import (
    AccountResolutionService,
)
from app.modules.connectors.services.connector_service import ConnectorService
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution

__all__ = ["ConnectorOperationService", "ResolvedConnectorExecution"]
from app.modules.connectors.services.execution.plumbing import (
    build_dispatcher,
    execution_failures_translated,
    execution_request,
)
from app.modules.connectors.services.credential_freshness import (
    resolve_execution_credentials,
)


class ConnectorOperationService:
    def __init__(
        self,
        *,
        connector_repository: ConnectorRepositoryPort,
        operation_repository: ConnectorOperationRepositoryPort,
        operation_gateway: AppOperationGatewayPort,
        account_resolution_service: AccountResolutionService,
        connector_service: ConnectorService | None = None,
        auth_config_operation_repository: Any | None = None,
    ):
        self.connector_repository = connector_repository
        self.operation_repository = operation_repository
        self.operation_gateway = operation_gateway
        self.account_resolution_service = account_resolution_service
        self.connector_service = connector_service
        self.auth_config_operation_repository = auth_config_operation_repository
        self._kind_dispatcher = None

    async def _get_connector(self, connector_id: str):
        connector = await self.connector_repository.get(connector_id)
        if not connector:
            raise ConnectorNotFoundError(connector_id)
        return connector

    async def _list_operation_entities(
        self,
        connector_id: str,
        *,
        kind: str | None = None,
        search_query: str | None = None,
        limit: int | None = None,
        auth_config_id: UUID | None = None,
    ) -> list[Any]:
        await self._get_connector(connector_id)
        return await list_operations_for_install(
            catalog_repository=self.operation_repository,
            install_repository=self.auth_config_operation_repository,
            connector_id=connector_id, kind=kind, auth_config_id=auth_config_id,
            search_query=search_query, limit=limit,
        )

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
        return auth_config, auth_config.connector_id, auth_config.kind.value

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
        return await resolve_execution_credentials(
            account,
            user_id,
            connector_service=self.connector_service,
            serialize=self._serialize_credentials,
            is_oauth=self._is_oauth_account,
        )

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
        auth_config, connector_id, kind = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.discover_operations(
            connector_id,
            query=query,
            limit=limit,
            kind=kind,
            auth_config_id=auth_config.id,
        )

    async def discover_operations(
        self,
        connector_id: str,
        query: str | None = None,
        limit: int | None = None,
        kind: str | None = None,
        auth_config_id: UUID | None = None,
    ) -> OperationDiscoverResponse:
        selected_operations = await self._list_operation_entities(
            connector_id,
            kind=kind,
            search_query=query,
            limit=limit,
            auth_config_id=auth_config_id,
        )
        # `total_operations` is the install's whole set, so a client can say
        # "showing 10 of 340". Only a narrowed selection needs a second read to
        # learn it -- an unfiltered, unlimited listing already *is* the total.
        if query is None and limit is None:
            total_operations = len(selected_operations)
        else:
            total_operations = len(
                await self._list_operation_entities(
                    connector_id, kind=kind, auth_config_id=auth_config_id
                )
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
        kind: str | None = None,
        auth_config_id: UUID | None = None,
    ) -> OperationDetail:
        await self._get_connector(connector_id)
        operation = await find_install_or_catalog_operation(
            catalog_repository=self.operation_repository,
            install_repository=self.auth_config_operation_repository,
            connector_id=connector_id,
            kind=kind,
            operation_name=operation_name,
            auth_config_id=auth_config_id,
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
        auth_config, connector_id, kind = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.get_operation_details(
            connector_id,
            operation_name,
            kind=kind,
            auth_config_id=auth_config.id,
        )

    async def get_operation_details_batch(
        self,
        connector_id: str,
        operation_names: list[str] | None = None,
        kind: str | None = None,
        auth_config_id: UUID | None = None,
    ) -> OperationDetailsBatchResponse:
        operations = await self._list_operation_entities(
            connector_id,
            kind=kind,
            auth_config_id=auth_config_id,
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
        auth_config, connector_id, kind = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.get_operation_details_batch(
            connector_id,
            operation_names=operation_names,
            kind=kind,
            auth_config_id=auth_config.id,
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
        auth_config, connector_id, _kind = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return await self.resolve_execution(
            connector_id=connector_id,
            operation_name=operation_name,
            payload=payload,
            user_id=user_id,
            actor=actor,
            auth_token=auth_token,
            api_url=api_url,
            account_id=account_id,
            auth_config_id=auth_config.id,
            # Already loaded by name above; re-reading it by id was a wasted
            # round trip on every execution.
            auth_config=auth_config,
        )

    async def resolve_execution(
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
        auth_config: Any | None = None,
    ) -> ResolvedConnectorExecution:
        kind: str | None = None
        if auth_config_id is not None:
            if self.connector_service is None:
                raise ConnectorNotFoundError(connector_id)
            if auth_config is None:
                auth_config = await self.connector_service.auth_config_repository.get(
                    auth_config_id
                )
            if auth_config is None:
                raise ConnectorNotFoundError(str(auth_config_id))
            kind = auth_config.kind.value
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
                    kind = auth_config.kind.value

        # An install's own discovered operation wins over a catalog one of the
        # same name: for mcp/openapi the catalog has nothing to offer, and where
        # both exist the install describes the server actually being called.
        operation = None
        if auth_config_id is not None and self.auth_config_operation_repository:
            operation = (
                await self.auth_config_operation_repository.get_by_auth_config_and_name(
                    auth_config_id, operation_name
                )
            )
        if operation is None:
            if kind:
                operation = (
                    await self.operation_repository.get_by_connector_kind_and_name(
                        connector_id, kind, operation_name
                    )
                )
            else:
                operation = await self.operation_repository.get_by_connector_and_name(
                    connector_id, operation_name
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
            # The gateway still routes on the legacy provider vocabulary, so map
            # the install's kind back onto it. Always concrete, never None: that
            # is what lets the gateway skip its connector-validation read so the
            # external call holds NO DB connection.
            provider=(
                kind_to_provider(kind).value if kind else AuthProvider.LEMMA.value
            ),
            kind=kind or ConnectorKind.PACKAGE.value,
            connection_config=(
                getattr(auth_config, "config", None) if auth_config else None
            ),
            execution=getattr(operation, "execution", None),
            operation_name=operation.name,
            input_schema=getattr(operation, "input_schema", None),
            third_party_credentials=third_party_credentials,
            payload=payload or {},
            auth_token=auth_token,
            api_url=api_url,
            account_id=getattr(account, "id", None),
            account_user_id=getattr(account, "user_id", None),
            organization_id=getattr(account, "organization_id", None),
        )

    def _dispatcher(self):
        if self._kind_dispatcher is None:
            self._kind_dispatcher = build_dispatcher(self.operation_gateway)
        return self._kind_dispatcher

    async def execute_resolved(
        self, resolved: ResolvedConnectorExecution
    ) -> OperationExecutionResponse:
        """Run the external operation for an already-resolved plan. Holds NO DB
        connection: the gateway's connector-validation read is skipped because
        ``provider`` is supplied (the connector was validated in the resolve
        phase), and the concrete provider gateways are DB-free."""
        with execution_failures_translated():
            result = await self._dispatcher().execute(
                execution_request(self._dispatcher(), resolved)
            )
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
        resolved = await self.resolve_execution(
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
