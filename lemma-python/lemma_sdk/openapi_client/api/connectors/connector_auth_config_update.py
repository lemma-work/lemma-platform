from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.auth_config_update_response_schema import AuthConfigUpdateResponseSchema
from ...models.auth_config_update_schema import AuthConfigUpdateSchema
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    auth_config_name: str,
    *,
    body: AuthConfigUpdateSchema,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}".format(
            organization_id=quote(str(organization_id), safe=""),
            auth_config_name=quote(str(auth_config_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AuthConfigUpdateResponseSchema | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AuthConfigUpdateResponseSchema.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AuthConfigUpdateResponseSchema | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    auth_config_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AuthConfigUpdateSchema,
) -> Response[AuthConfigUpdateResponseSchema | ErrorResponse]:
    """Update Auth Config

     Update an install in place. Rotating an MCP server URL or an OAuth app no longer requires deleting
    the install, which cascades away every account connected to it. Accounts are never deleted here: if
    the change invalidates their credentials they are marked REAUTH_REQUIRED, and reconnecting updates
    them in place. `kind`, `connector_id` and `config_source` are immutable -- changing any of them
    reinterprets every stored operation and credential, so that is a new install rather than an update.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        body (AuthConfigUpdateSchema):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuthConfigUpdateResponseSchema | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    auth_config_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AuthConfigUpdateSchema,
) -> AuthConfigUpdateResponseSchema | ErrorResponse | None:
    """Update Auth Config

     Update an install in place. Rotating an MCP server URL or an OAuth app no longer requires deleting
    the install, which cascades away every account connected to it. Accounts are never deleted here: if
    the change invalidates their credentials they are marked REAUTH_REQUIRED, and reconnecting updates
    them in place. `kind`, `connector_id` and `config_source` are immutable -- changing any of them
    reinterprets every stored operation and credential, so that is a new install rather than an update.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        body (AuthConfigUpdateSchema):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuthConfigUpdateResponseSchema | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    auth_config_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AuthConfigUpdateSchema,
) -> Response[AuthConfigUpdateResponseSchema | ErrorResponse]:
    """Update Auth Config

     Update an install in place. Rotating an MCP server URL or an OAuth app no longer requires deleting
    the install, which cascades away every account connected to it. Accounts are never deleted here: if
    the change invalidates their credentials they are marked REAUTH_REQUIRED, and reconnecting updates
    them in place. `kind`, `connector_id` and `config_source` are immutable -- changing any of them
    reinterprets every stored operation and credential, so that is a new install rather than an update.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        body (AuthConfigUpdateSchema):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AuthConfigUpdateResponseSchema | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    auth_config_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AuthConfigUpdateSchema,
) -> AuthConfigUpdateResponseSchema | ErrorResponse | None:
    """Update Auth Config

     Update an install in place. Rotating an MCP server URL or an OAuth app no longer requires deleting
    the install, which cascades away every account connected to it. Accounts are never deleted here: if
    the change invalidates their credentials they are marked REAUTH_REQUIRED, and reconnecting updates
    them in place. `kind`, `connector_id` and `config_source` are immutable -- changing any of them
    reinterprets every stored operation and credential, so that is a new install rather than an update.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        body (AuthConfigUpdateSchema):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AuthConfigUpdateResponseSchema | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            auth_config_name=auth_config_name,
            client=client,
            body=body,
        )
    ).parsed
