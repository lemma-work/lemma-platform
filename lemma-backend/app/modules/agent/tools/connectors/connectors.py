from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.api.dependencies import build_connector_operation_service
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDetailsBatchResponse,
    OperationDiscoverResponse,
)
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)
from app.core.infrastructure.events.message_bus import get_message_bus


class _ConnectorInfoToolDeps(BaseModel):
    allowed_app_names: list[str]
    service: ConnectorOperationService | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class _OperationSearchToolRequest(BaseModel):
    connector_id: str
    query: str | None = None
    limit: int | None = None


class _OperationDetailsToolRequest(BaseModel):
    connector_id: str
    operation_names: list[str] | None = None


def _ensure_app_allowed(
    allowed_app_names: list[str],
    connector_id: str,
) -> None:
    # Raise ModelRetry (not ValueError) so the helper sub-agent gets a corrective
    # message and can re-issue against an allowed app, instead of aborting the run.
    if connector_id not in allowed_app_names:
        allowed = ", ".join(allowed_app_names) or "(none)"
        raise ModelRetry(
            f"Connector '{connector_id}' is not allowed for this helper call. "
            f"Allowed connectors: {allowed}."
        )


async def search_operations_tool(
    ctx: RunContext[_ConnectorInfoToolDeps],
    request: _OperationSearchToolRequest,
) -> OperationDiscoverResponse:
    """Search operations for one connector using only name and description."""

    _ensure_app_allowed(ctx.deps.allowed_app_names, request.connector_id)
    return await _with_connector_operation_service(
        ctx.deps,
        lambda service: service.discover_operations(
            request.connector_id,
            query=request.query,
            limit=request.limit,
        ),
    )


async def get_operation_details_tool(
    ctx: RunContext[_ConnectorInfoToolDeps],
    request: _OperationDetailsToolRequest,
) -> OperationDetailsBatchResponse:
    """Fetch detailed input and output schemas for one or more operations."""

    _ensure_app_allowed(ctx.deps.allowed_app_names, request.connector_id)
    return await _with_connector_operation_service(
        ctx.deps,
        lambda service: service.get_operation_details_batch(
            request.connector_id,
            operation_names=request.operation_names,
        ),
    )


async def _with_connector_operation_service(
    deps: _ConnectorInfoToolDeps,
    callback: Callable[
        [ConnectorOperationService],
        Awaitable[OperationDiscoverResponse | OperationDetailsBatchResponse],
    ],
) -> OperationDiscoverResponse | OperationDetailsBatchResponse:
    if deps.service is not None:
        return await callback(deps.service)

    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session, message_bus=get_message_bus())
        service = build_connector_operation_service(uow)
        return await callback(service)


connector_info_toolset = FunctionToolset[_ConnectorInfoToolDeps](
    tools=[
        search_operations_tool,
        get_operation_details_tool,
    ]
)
