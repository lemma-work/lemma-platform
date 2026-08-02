from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.connector_auth_config_refresh_operations_response_connector_auth_config_refresh_operations import (
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations,
)
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    auth_config_name: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}/operations/refresh".format(
            organization_id=quote(str(organization_id), safe=""),
            auth_config_name=quote(str(auth_config_name), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> (
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations
    | ErrorResponse
    | None
):
    if response.status_code == 200:
        response_200 = ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations.from_dict(
            response.json()
        )

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
) -> Response[
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations
    | ErrorResponse
]:
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
) -> Response[
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations
    | ErrorResponse
]:
    """Refresh Auth Config Operations

     Re-discover the operations exposed by a discovery-based install (MCP server, OpenAPI URL). Use after
    the upstream server changes its tools, or to retry a discovery that failed when the install was
    created.

    Args:
        organization_id (UUID):
        auth_config_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
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
) -> (
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations
    | ErrorResponse
    | None
):
    """Refresh Auth Config Operations

     Re-discover the operations exposed by a discovery-based install (MCP server, OpenAPI URL). Use after
    the upstream server changes its tools, or to retry a discovery that failed when the install was
    created.

    Args:
        organization_id (UUID):
        auth_config_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        client=client,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    auth_config_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations
    | ErrorResponse
]:
    """Refresh Auth Config Operations

     Re-discover the operations exposed by a discovery-based install (MCP server, OpenAPI URL). Use after
    the upstream server changes its tools, or to retry a discovery that failed when the install was
    created.

    Args:
        organization_id (UUID):
        auth_config_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    auth_config_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> (
    ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations
    | ErrorResponse
    | None
):
    """Refresh Auth Config Operations

     Re-discover the operations exposed by a discovery-based install (MCP server, OpenAPI URL). Use after
    the upstream server changes its tools, or to retry a discovery that failed when the install was
    created.

    Args:
        organization_id (UUID):
        auth_config_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectorAuthConfigRefreshOperationsResponseConnectorAuthConfigRefreshOperations | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            auth_config_name=auth_config_name,
            client=client,
        )
    ).parsed
