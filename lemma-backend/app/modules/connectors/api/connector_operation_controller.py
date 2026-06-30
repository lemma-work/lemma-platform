from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.api.dependencies import CurrentUser
from app.modules.connectors.api.dependencies import (
    ConnectorOperationServiceDep,
    ConnectorOperationUseCasesDep,
)
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDetail,
    OperationDetailsBatchRequest,
    OperationDetailsBatchResponse,
    OperationDiscoverResponse,
    OperationExecutionRequest,
    OperationExecutionResponse,
)

router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/{auth_config_name}/operations",
    tags=["Connectors"],
)


@router.get(
    "",
    response_model=OperationDiscoverResponse,
    operation_id="connector.operation.discover",
    summary="Discover Connector Operations",
)
async def discover_operations(
    organization_id: UUID,
    auth_config_name: str,
    user: CurrentUser,
    service: ConnectorOperationServiceDep,
    query: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> OperationDiscoverResponse:
    return await service.discover_operations_for_auth_config(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        query=query,
        limit=limit,
    )


@router.post(
    "/details",
    response_model=OperationDetailsBatchResponse,
    operation_id="connector.operation.details.batch",
    summary="Get Connector Operation Details In Batch",
)
async def get_operation_details_batch(
    organization_id: UUID,
    auth_config_name: str,
    body: OperationDetailsBatchRequest,
    user: CurrentUser,
    service: ConnectorOperationServiceDep,
) -> OperationDetailsBatchResponse:
    return await service.get_operation_details_batch_for_auth_config(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_names=body.operation_names,
    )


@router.get(
    "/{operation_name}",
    response_model=OperationDetail,
    operation_id="connector.operation.detail",
    summary="Get Connector Operation Details",
)
async def get_operation_details(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    user: CurrentUser,
    service: ConnectorOperationServiceDep,
) -> OperationDetail:
    return await service.get_operation_details_for_auth_config(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_name=operation_name,
    )


@router.post(
    "/{operation_name}/execute",
    response_model=OperationExecutionResponse,
    operation_id="connector.operation.execute",
    summary="Execute Connector Operation",
)
async def execute_operation(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    body: OperationExecutionRequest,
    request: Request,
    user: CurrentUser,
    use_cases: ConnectorOperationUseCasesDep,
) -> OperationExecutionResponse:
    # No request-scoped service/context dependency: the use-case authorizes +
    # resolves inside a short DB scope and runs the (1-45s) external operation
    # call with no pooled connection held. Authorization is preserved -- the
    # org/delegation Context is built in-scope and threaded as the actor.
    auth_header = request.headers.get("authorization") or ""
    auth_token = None
    if auth_header.lower().startswith("bearer "):
        auth_token = auth_header.split(" ", 1)[1].strip()

    account_id = UUID(body.account_id) if body.account_id else None
    return await use_cases.execute_operation_for_auth_config(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_name=operation_name,
        payload=body.payload,
        user_id=user.id,
        request=request,
        auth_token=auth_token,
        api_url=str(request.base_url).rstrip("/"),
        account_id=account_id,
    )
