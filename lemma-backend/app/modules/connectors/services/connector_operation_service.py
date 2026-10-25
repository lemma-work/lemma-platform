from __future__ import annotations

import base64
from typing import Any
from uuid import UUID


from app.core.authorization.context import Context
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    MAX_OPERATION_DETAILS_PER_REQUEST,
    OperationDetail,
    OperationDetailsBatchResponse,
    OperationDiscoverResponse,
    OperationExecutionResponse,
    OperationSummary,
)
from app.modules.connectors.domain.connector import kind_to_provider
from app.modules.connectors.domain.errors import (
    ConnectorNotFoundError,
    OperationNotFoundError,
)
from app.modules.connectors.domain.ports import (
    ConnectorOperationRepositoryPort,
    ConnectorRepositoryPort,
    AppOperationGatewayPort,
)
from app.modules.connectors.services.operation_ranking import (
    operation_relevance_score,
)
from app.modules.connectors.services.operation_visibility import (
    count_operations_for_install,
    named_operations,
    find_install_or_catalog_operation,
    list_operations_for_install,
)
from app.modules.connectors.services.pod_connector_resolution import (
    PodConnectorResolver,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.services.auth.github_bindings import routable_external_ref
from app.modules.connectors.services.connector_service import ConnectorService
from app.modules.connectors.domain.analytics import operation_execution_recorded
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution

__all__ = ["ConnectorOperationService", "ResolvedConnectorExecution"]
from app.modules.connectors.services.execution.plumbing import (
    build_dispatcher,
    execution_failures_translated,
    execution_request,
)
from app.modules.connectors.services.credential_freshness import (
    is_oauth_account,
    resolve_execution_credentials,
    serialize_credentials,
)


class ConnectorOperationService:
    def __init__(
        self,
        *,
        connector_repository: ConnectorRepositoryPort,
        operation_repository: ConnectorOperationRepositoryPort,
        operation_gateway: AppOperationGatewayPort,
        pod_connector_resolver: PodConnectorResolver,
        connector_service: ConnectorService | None = None,
        auth_config_operation_repository: Any | None = None,
    ):
        self.connector_repository = connector_repository
        self.operation_repository = operation_repository
        self.operation_gateway = operation_gateway
        self.pod_connector_resolver = pod_connector_resolver
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
            connector_id=connector_id,
            kind=kind,
            auth_config_id=auth_config_id,
            search_query=search_query,
            limit=limit,
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
            relevance_score=operation_relevance_score(operation, query),
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
        return serialize_credentials(credentials)

    def _is_oauth_account(self, account: Any) -> bool:
        return is_oauth_account(account)

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

    async def _routable_external_ref(self, account: AccountEntity) -> str | None:
        """The tenant the call may act on: for GitHub, the installation an App
        token is minted for, and only while it is bound to the account's own
        organization (audit H4)."""
        if not account.external_ref or self.connector_service is None:
            return None
        return await routable_external_ref(self.connector_service.uow.session, account)

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
        # And that read is a count, not a second listing: `len()` over every
        # row with its JSONB schemas is not a count query, and the agent's
        # cross-install search fans this out over every install in the org.
        if query is None and limit is None:
            total_operations = len(selected_operations)
        else:
            total_operations = await count_operations_for_install(
                catalog_repository=self.operation_repository,
                install_repository=self.auth_config_operation_repository,
                connector_id=connector_id,
                kind=kind,
                auth_config_id=auth_config_id,
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
        limit: int = MAX_OPERATION_DETAILS_PER_REQUEST,
    ) -> OperationDetailsBatchResponse:
        """Full schemas for several operations at once.

        Bounded even when no names are given. A detail carries the operation's
        whole input and output schema, so "every operation" on a connector the
        size of Jira is tens of megabytes of JSON built in memory -- and the
        endpoint documented that as its intended usage, which made it the
        cheapest way for any org member to exhaust the API's memory.
        """
        if operation_names:
            await self._get_connector(connector_id)
            selected_operations = await named_operations(
                catalog_repository=self.operation_repository,
                install_repository=self.auth_config_operation_repository,
                connector_id=connector_id,
                operation_names=operation_names,
                kind=kind,
                auth_config_id=auth_config_id,
            )
        else:
            # `limit` down into the read rather than a slice over everything:
            # a detail carries the operation's whole input and output schema,
            # so reading the connector's catalog to return `limit` of them is
            # the cost this endpoint's own docstring warns about, paid anyway.
            selected_operations = list(
                await self._list_operation_entities(
                    connector_id,
                    kind=kind,
                    auth_config_id=auth_config_id,
                    limit=limit,
                )
            )[:limit]

        items = [
            self._build_operation_detail(operation) for operation in selected_operations
        ]
        return OperationDetailsBatchResponse(
            connector_id=connector_id,
            items=items,
            returned_count=len(items),
            # A count, not `len()` of a listing that no longer exists. It is the
            # number the client sizes the connector by -- "showing 10 of 340" --
            # and it was the last reason to read every row.
            total_operations=await count_operations_for_install(
                catalog_repository=self.operation_repository,
                install_repository=self.auth_config_operation_repository,
                connector_id=connector_id,
                kind=kind,
                auth_config_id=auth_config_id,
            ),
        )

    async def get_operation_details_batch_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        operation_names: list[str] | None = None,
        limit: int = MAX_OPERATION_DETAILS_PER_REQUEST,
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
            limit=limit,
        )

    # -- Resolve / execute split ------------------------------------------------
    # ``resolve_pod_connector_execution`` does all the DB reads + authorization
    # + credential resolution and returns a session-free
    # ``ResolvedConnectorExecution``; ``execute_resolved`` performs only the
    # external gateway call. The ConnectorOperationUseCases runs resolve in a
    # short DB scope and execute with no connection held.

    async def pod_connector_operations(
        self,
        ctx: Context,
        *,
        pod_id: UUID,
        slug: str,
        query: str | None = None,
        limit: int | None = None,
    ) -> OperationDiscoverResponse:
        """The operations a pod connector offers, to someone who may run them."""
        _, auth_config = await self._usable_pod_connector(ctx, pod_id, slug)
        return await self.discover_operations(
            auth_config.connector_id,
            query=query,
            limit=limit,
            kind=auth_config.kind.value,
            auth_config_id=auth_config.id,
        )

    async def pod_connector_operation(
        self, ctx: Context, *, pod_id: UUID, slug: str, operation_name: str
    ) -> OperationDetail:
        _, auth_config = await self._usable_pod_connector(ctx, pod_id, slug)
        return await self.get_operation_details(
            auth_config.connector_id,
            operation_name,
            kind=auth_config.kind.value,
            auth_config_id=auth_config.id,
        )

    async def _usable_pod_connector(self, ctx: Context, pod_id: UUID, slug: str):
        connector = await self.pod_connector_resolver.connector(pod_id, slug)
        if self.connector_service is None:
            raise ConnectorNotFoundError(slug)
        auth_config = await self.connector_service.auth_config_repository.get(
            connector.auth_config_id
        )
        if auth_config is None:
            raise ConnectorNotFoundError(slug)
        await self.pod_connector_resolver.require_use(ctx, connector, auth_config)
        return connector, auth_config

    async def resolve_pod_connector_execution(
        self,
        ctx: Context,
        *,
        pod_id: UUID,
        slug: str,
        operation_name: str,
        payload: dict[str, Any],
        act_as: str = "user",
    ) -> ResolvedConnectorExecution:
        """Authorize, pick the account, and load everything the call needs.

        The account is never an input. The pod connector's mode and the actor
        decide it (``PodConnectorResolver.resolve``), so nothing a caller types
        -- a model least of all -- chooses whose credential a call runs as.
        """
        pod_connector = await self.pod_connector_resolver.connector(pod_id, slug)
        resolved = await self.pod_connector_resolver.resolve(ctx, pod_connector)
        return await self.execution_for(
            ctx,
            auth_config=resolved.auth_config,
            account=resolved.account,
            operation_name=operation_name,
            payload=payload,
            act_as=act_as,
        )

    async def execution_for(
        self,
        ctx: Context,
        *,
        auth_config: AuthConfigEntity,
        account: AccountEntity,
        operation_name: str,
        payload: dict[str, Any],
        act_as: str,
    ) -> ResolvedConnectorExecution:
        connector_id = auth_config.connector_id
        kind = auth_config.kind.value

        # An install's discovered operation wins over a catalog one of the same
        # name: the install describes the server actually being called.
        operation = None
        if self.auth_config_operation_repository:
            operation = (
                await self.auth_config_operation_repository.get_by_auth_config_and_name(
                    auth_config.id, operation_name
                )
            )
        if operation is None:
            operation = await self.operation_repository.get_by_connector_kind_and_name(
                connector_id, kind, operation_name
            )
        if not operation:
            raise OperationNotFoundError(operation_name)

        credentials = await self._resolve_execution_credentials(
            account, account.user_id
        )
        return ResolvedConnectorExecution(
            connector_id=connector_id,
            operation_execution_name=operation.execution_name,
            # The gateway still routes on the legacy provider vocabulary, so map
            # the install's kind back onto it. Always concrete: that is what lets
            # the gateway skip its connector-validation read so the external call
            # holds NO DB connection.
            provider=kind_to_provider(kind).value,
            kind=kind,
            connection_config=getattr(auth_config, "config", None),
            execution=getattr(operation, "execution", None),
            operation_name=operation.name,
            input_schema=getattr(operation, "input_schema", None),
            third_party_credentials=credentials,
            payload=payload or {},
            account_id=account.id,
            account_external_ref=await self._routable_external_ref(account),
            act_as=act_as,
            account_user_id=account.user_id,
            acting_user_id=ctx.delegating_user_id or ctx.user_id,
            organization_id=account.organization_id,
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
        with operation_execution_recorded(resolved), execution_failures_translated():
            result = await self._dispatcher().execute(
                execution_request(self._dispatcher(), resolved)
            )
        return OperationExecutionResponse(
            result=self._normalize_execution_result(result)
        )
