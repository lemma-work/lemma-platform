"""Connector-operation execution saga.

Mirrors ``FunctionUseCases``: built from a ``uow_factory`` + a per-phase service
builder so the DB/auth resolve phase runs inside a SHORT unit-of-work scope and
the external Composio/Lemma operation call runs with NO pooled DB connection
held. A request-scoped service/context dependency would otherwise pin one pooled
connection per in-flight connector call (every ``pod.connectors.execute(...)``
from a function routes through here), exhausting the pool under load.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi import Request

from app.core.authorization.scope import current_context_scope, uow_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationExecutionResponse,
)
from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
    ResolvedConnectorExecution,
)


class ConnectorOperationUseCases:
    """Owns the connector-operation execution saga (factory mode)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[Any], ConnectorOperationService],
        pod_file_gateway_factory: Callable[[Any], Any] | None = None,
    ):
        self._uow_factory = uow_factory
        self._build = service_builder
        # Supplied at composition, because reaching the pod datastore is where
        # the connectors and datastore modules meet. None means "no pod context",
        # and file results simply come back inline.
        self._pod_file_gateway_factory = pod_file_gateway_factory or (lambda _uow: None)

    async def execute_operation_for_auth_config(
        self,
        *,
        organization_id: UUID,
        auth_config_name: str,
        operation_name: str,
        payload: dict[str, Any],
        user_id: UUID,
        request: Request,
        auth_token: str | None = None,
        api_url: str | None = None,
        account_id: UUID | None = None,
    ) -> OperationExecutionResponse:
        # Phase 1 (short scope): build + bind the request Context (org/delegation
        # aware), resolve all DB state + authorize + resolve credentials. The
        # scope commits any OAuth-token refresh and releases the connection on
        # exit, before the external call.
        async with current_context_scope(
            self._uow_factory, request=request, user_id=user_id
        ) as scope:
            resolved = await self._build(scope.uow).resolve_execution_for_auth_config(
                user_id=user_id,
                organization_id=organization_id,
                auth_config_name=auth_config_name,
                operation_name=operation_name,
                payload=payload,
                actor=scope.ctx,
                auth_token=auth_token,
                api_url=api_url,
                account_id=account_id,
            )

        # Phase 2: the external operation call, with NO pooled connection held.
        # ``execute_resolved`` issues no DB I/O -- the gateway's connector
        # validation is skipped (``resolved.provider`` is always set) and the
        # concrete Lemma/Composio gateways are DB-free -- so this short uow never
        # checks out a connection across the (1-45s) external call. The scope only
        # supplies the service collaborator that owns the gateway + timeout +
        # error-mapping logic.
        try:
            async with uow_scope(self._uow_factory) as uow:
                response = await self._build(uow).execute_resolved(resolved)
        except (
            OperationExecutionUnauthorizedError,
            OperationExecutionAccessDeniedError,
        ):
            # The provider rejected our credentials: the account is unusable until
            # the user reconnects. Flag it in a fresh short scope (the external
            # call already finished, so no connection was held across it), then
            # re-raise the original error unchanged.
            await self._flag_account_reauth_required(resolved)
            raise

        # Phase 3: if the result carries a file, decide what the caller actually
        # receives -- inline bytes for something small, a pod-datastore reference
        # for something large. Its own short scope, after the external call.
        return await self._capture_binary_output(
            response,
            payload=payload,
            user_id=user_id,
            request=request,
            connector_id=resolved.connector_id,
        )

    async def _capture_binary_output(
        self,
        response: OperationExecutionResponse,
        *,
        payload: dict[str, Any],
        user_id: UUID,
        request: Request,
        connector_id: str,
    ) -> OperationExecutionResponse:
        """Return a usable file, whatever shape the provider wrapped it in.

        Detection is by shape anywhere in the result rather than one envelope at
        the top level, which is why a Composio download -- nested under ``data``
        in Composio's own envelope -- now resolves at all. Persisting is decided
        by size; ``output_path`` only chooses the destination.
        """
        from app.modules.connectors.services.files.capture import find_binary
        from app.modules.connectors.services.files.capture_writer import (
            BinaryResultWriter,
        )

        result = getattr(response, "result", None)
        if find_binary(result) is None:
            return response

        async with current_context_scope(
            self._uow_factory, request=request, user_id=user_id
        ) as scope:
            gateway = self._pod_file_gateway_factory(scope.uow)
            captured = await BinaryResultWriter(gateway).capture(
                result,
                connector_id=connector_id,
                pod_id=getattr(scope.ctx, "pod_id", None),
                ctx=scope.ctx,
                output_path=(payload or {}).get("output_path"),
            )
        return OperationExecutionResponse(result=captured)

    async def _flag_account_reauth_required(
        self, resolved: ResolvedConnectorExecution
    ) -> None:
        if resolved.account_id is None or resolved.account_user_id is None:
            return
        async with uow_scope(self._uow_factory) as uow:
            connector_service = self._build(uow).connector_service
            if connector_service is None:
                return
            await connector_service.mark_account_reauth_required(
                resolved.account_id,
                resolved.account_user_id,
                resolved.organization_id,
            )
