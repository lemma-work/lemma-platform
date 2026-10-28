from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.api.dependencies import CurrentUser
from app.core.app_pin import app_pod_for
from app.core.api.deprecation import deprecated_route
from app.modules.connectors.api.dependencies import (
    ConnectorOperationServiceDep,
    ConnectorOperationUseCasesDep,
)
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDetail,
    OperationDetailsBatchRequest,
    OperationDetailsBatchResponse,
    OperationDiscoverResponse,
    OperationExecutionResponse,
    InstallationOperationExecutionRequest,
)
from app.modules.connectors.contracts.deprecations import (
    ACCOUNT_ADDRESSING_SUNSET,
    EXECUTE_REPLACEMENT,
)

router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/{auth_config_name}/operations",
    tags=["Connectors"],
)

# Org-wide search lives outside the per-install prefix: its whole point is not
# needing an install name.
org_router = APIRouter(
    prefix="/organizations/{organization_id}/connector-operations",
    tags=["Connectors"],
)


@org_router.get(
    "",
    response_model=OperationDiscoverResponse,
    operation_id="connector.operation.search",
    summary="Search Connector Operations Across Installs",
    description=(
        "Search operations across every connector installed in the org. Each hit "
        "carries the `auth_config` to execute it against, so a caller that knows "
        "what it wants to do — but not which connector provides it — needs one "
        "request instead of one per install."
    ),
)
async def search_operations(
    organization_id: UUID,
    user: CurrentUser,
    service: ConnectorOperationServiceDep,
    query: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> OperationDiscoverResponse:
    from app.modules.connectors.services.connector_operation_search import (
        search_across_auth_configs,
    )

    return await search_across_auth_configs(
        service,
        user_id=user.id,
        organization_id=organization_id,
        query=query,
        limit=limit,
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
        limit=body.limit,
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
    summary="Execute Connector Operation (deprecated)",
    description=(
        "Deprecated: use `pod.connector.operation.execute`. Still served, "
        "through the connectors the caller's pod declares for this "
        "installation; a person outside any pod uses their own account."
    ),
    **deprecated_route(
        operation_id="connector.operation.execute",
        replacement=EXECUTE_REPLACEMENT,
        sunset=ACCOUNT_ADDRESSING_SUNSET,
    ),
)
async def execute_operation(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    body: InstallationOperationExecutionRequest,
    request: Request,
    user: CurrentUser,
    use_cases: ConnectorOperationUseCasesDep,
) -> OperationExecutionResponse:
    # No request-scoped service or context: the use case authorizes and
    # resolves in a short scope and makes the external call holding no pooled
    # connection, the same saga as the pod route.
    #
    # The pod comes from the body, which the app pin's path check cannot see:
    # an app's page runs in its own pod, whether it names it or not.
    pod_id = app_pod_for(request, body.pod_id)
    return await use_cases.execute_installation_operation(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_name=operation_name,
        payload=body.payload,
        account_id=UUID(body.account_id) if body.account_id else None,
        pod_id=pod_id,
        user_id=user.id,
        request=request,
    )
