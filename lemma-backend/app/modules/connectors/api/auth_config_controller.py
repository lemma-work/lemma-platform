from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.core.api.dependencies import CurrentUser
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import reject_delegated_workload
from app.modules.connectors.api.dependencies import ConnectorServiceDep
from app.modules.connectors.api.schemas import (
    AuthConfigCreateSchema,
    AuthConfigListResponseSchema,
    AuthConfigResponseSchema,
    ConnectorStatusResponse,
)

router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/auth-configs",
    tags=["Connectors"],
)

status_router = APIRouter(
    prefix="/organizations/{organization_id}/connectors",
    tags=["Connectors"],
)


@status_router.get(
    "/status",
    response_model=ConnectorStatusResponse,
    operation_id="connector.status.get",
)
async def get_connector_status(
    user: CurrentUser,
    organization_id: UUID,
    connector_service: ConnectorServiceDep,
) -> ConnectorStatusResponse:
    data = await connector_service.get_connector_status(
        user_id=user.id,
        organization_id=organization_id,
    )
    return ConnectorStatusResponse.model_validate(data)


def _redact_credential_config(value: dict | None) -> dict | None:
    if value is None:
        return None
    redacted = dict(value)
    oauth2_credentials = redacted.get("oauth2_credentials")
    if isinstance(oauth2_credentials, dict):
        oauth2_credentials = dict(oauth2_credentials)
        if oauth2_credentials.get("client_secret"):
            oauth2_credentials["client_secret"] = "********"
        redacted["oauth2_credentials"] = oauth2_credentials
    if redacted.get("client_secret"):
        redacted["client_secret"] = "********"
    return redacted


def _response_from_entity(entity) -> AuthConfigResponseSchema:
    data = entity.model_dump(mode="json")
    # `provider` is a computed view on `kind` now, so it is absent from the dump
    # and has to be put back explicitly. The wire shape is unchanged.
    data["provider"] = entity.provider.value
    data["credential_config"] = _redact_credential_config(data.pop("config", None))
    return AuthConfigResponseSchema.model_validate(data)


@router.post(
    "",
    response_model=AuthConfigResponseSchema,
    operation_id="connector.auth_config.create",
)
async def create_auth_config(
    user: CurrentUser,
    organization_id: UUID,
    data: AuthConfigCreateSchema,
    connector_service: ConnectorServiceDep,
) -> AuthConfigResponseSchema:
    auth_config = await connector_service.create_auth_config(
        user_id=user.id,
        organization_id=organization_id,
        connector_id=data.connector_id,
        provider=data.provider,
        config_source=data.config_source,
        provider_config=data.credential_config,
        name=data.name,
    )
    return _response_from_entity(auth_config)


@router.get(
    "",
    response_model=AuthConfigListResponseSchema,
    operation_id="connector.auth_config.list",
)
async def list_auth_configs(
    user: CurrentUser,
    organization_id: UUID,
    connector_service: ConnectorServiceDep,
    limit: int = Query(default=100),
    page_token: str | None = Query(default=None),
) -> AuthConfigListResponseSchema:
    try:
        cursor = parse_uuid_page_token(page_token)
    except ValueError:
        cursor = None
    items, next_cursor = await connector_service.list_auth_configs(
        user_id=user.id,
        organization_id=organization_id,
        limit=limit,
        cursor=cursor,
    )
    return AuthConfigListResponseSchema(
        items=[_response_from_entity(item) for item in items],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/{auth_config_name}",
    response_model=AuthConfigResponseSchema,
    operation_id="connector.auth_config.get",
)
async def get_auth_config(
    user: CurrentUser,
    organization_id: UUID,
    auth_config_name: str,
    connector_service: ConnectorServiceDep,
) -> AuthConfigResponseSchema:
    auth_config = await connector_service.get_auth_config_by_name(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
    )
    return _response_from_entity(auth_config)


@router.delete(
    "/{auth_config_name}",
    operation_id="connector.auth_config.delete",
    dependencies=[reject_delegated_workload("delete an auth config")],
)
async def delete_auth_config(
    user: CurrentUser,
    organization_id: UUID,
    auth_config_name: str,
    connector_service: ConnectorServiceDep,
) -> dict[str, bool]:
    await connector_service.delete_auth_config(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
    )
    return {"success": True}
