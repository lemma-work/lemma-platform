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
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)


class ConnectorOperationUseCases:
    """Owns the connector-operation execution saga (factory mode)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        service_builder: Callable[[Any], ConnectorOperationService],
    ):
        self._uow_factory = uow_factory
        self._build = service_builder

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
        async with uow_scope(self._uow_factory) as uow:
            return await self._build(uow).execute_resolved(resolved)
