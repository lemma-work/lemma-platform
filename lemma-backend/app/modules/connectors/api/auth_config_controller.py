from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.api.dependencies import CurrentUser
from app.core.api.pagination import parse_uuid_page_token
from app.core.redaction import is_sensitive_key
from app.core.authorization.dependencies import reject_delegated_workload
from app.modules.connectors.api.dependencies import ConnectorServiceDep
from app.modules.connectors.api.schemas import (
    AuthConfigCreateSchema,
    AuthConfigListResponseSchema,
    AuthConfigResponseSchema,
    AuthConfigUpdateResponseSchema,
    AuthConfigUpdateSchema,
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


_REDACTED = "********"

# A location is not a credential. `is_sensitive_key` matches on substrings, so
# without this every `authorization_endpoint` and `token_endpoint` -- which an
# operator needs to see, and which appear in the authorize URL anyway -- would
# come back masked.
_PUBLIC_SUFFIXES = ("_endpoint", "_url", "_uri")


def _is_secret_field(key: object) -> bool:
    name = str(key).strip().lower()
    if name.endswith(_PUBLIC_SUFFIXES):
        return False
    return is_sensitive_key(key)


def _redact_config(value: dict | None) -> dict | None:
    """Mask every secret in an install config, at any depth.

    This used to name the two places a secret was known to live -- the top
    level and `oauth2_credentials`. That is a list which is only correct until
    something adds a third, and something did: RFC 7591 registration writes a
    `client_secret` under `oauth`, which the list did not visit, so the OAuth
    client credential the deployment registered with a tenant's authorization
    server was returned in full to any org member -- including a read-only one,
    since reading an install needs no role while creating one needs owner or
    editor.

    Recursing over the sensitive-key set instead means the next nesting is
    covered before it is written, rather than after someone notices.
    """
    if value is None:
        return None
    return _redacted(value)


def _redacted(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redacted_entry(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


def _redacted_entry(key: object, value: Any) -> Any:
    # A container under a secret-sounding key is not itself the secret --
    # `oauth2_credentials` holds a client id worth showing next to a client
    # secret worth hiding. Mask the leaves, keep the shape.
    if isinstance(value, (dict, list)):
        return _redacted(value)
    return _REDACTED if _is_secret_field(key) and value else value


def _response_from_entity(entity) -> AuthConfigResponseSchema:
    data = entity.model_dump(mode="json")
    data["config"] = _redact_config(data.get("config"))
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
        kind=data.kind,
        config_source=data.config_source,
        config=data.config,
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


@router.patch(
    "/{auth_config_name}",
    response_model=AuthConfigUpdateResponseSchema,
    operation_id="connector.auth_config.update",
    summary="Update Auth Config",
    description=(
        "Update an install in place. Rotating an MCP server URL or an OAuth "
        "app no longer requires deleting the install, which cascades away "
        "every account connected to it. Accounts are never deleted here: if "
        "the change invalidates their credentials they are marked "
        "REAUTH_REQUIRED, and reconnecting updates them in place. `kind`, "
        "`connector_id` and `config_source` are immutable -- changing any of "
        "them reinterprets every stored operation and credential, so that is a "
        "new install rather than an update."
    ),
    dependencies=[reject_delegated_workload("update an auth config")],
)
async def update_auth_config(
    user: CurrentUser,
    organization_id: UUID,
    auth_config_name: str,
    data: AuthConfigUpdateSchema,
    connector_service: ConnectorServiceDep,
) -> AuthConfigUpdateResponseSchema:
    auth_config, discovered, marked = await connector_service.update_auth_config(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        name=data.name,
        config=data.config,
        status=data.status,
        is_default=data.is_default,
    )
    return AuthConfigUpdateResponseSchema(
        auth_config=_response_from_entity(auth_config),
        operations_discovered=discovered,
        accounts_marked_for_reauth=marked,
    )


@router.post(
    "/{auth_config_name}/operations/refresh",
    operation_id="connector.auth_config.refresh_operations",
    summary="Refresh Auth Config Operations",
    description=(
        "Re-discover the operations exposed by a discovery-based install "
        "(MCP server, OpenAPI URL). Use after the upstream server changes its "
        "tools, or to retry a discovery that failed when the install was created."
    ),
)
async def refresh_auth_config_operations(
    user: CurrentUser,
    organization_id: UUID,
    auth_config_name: str,
    connector_service: ConnectorServiceDep,
) -> dict:
    count = await connector_service.refresh_auth_config_operations(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
    )
    return {"auth_config_name": auth_config_name, "operation_count": count}


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
