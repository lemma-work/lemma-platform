from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.installation_operation_execution_request import (
    InstallationOperationExecutionRequest,
)
from ...models.operation_execution_response import OperationExecutionResponse
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    *,
    body: InstallationOperationExecutionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/organizations/{organization_id}/connectors/{auth_config_name}/operations/{operation_name}/execute".format(
            organization_id=quote(str(organization_id), safe=""),
            auth_config_name=quote(str(auth_config_name), safe=""),
            operation_name=quote(str(operation_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OperationExecutionResponse | None:
    if response.status_code == 200:
        response_200 = OperationExecutionResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OperationExecutionResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationOperationExecutionRequest,
) -> Response[ErrorResponse | OperationExecutionResponse]:
    """Execute Connector Operation (deprecated)

     Deprecated: use `pod.connector.operation.execute`. Still served, through the connectors the caller's
    pod declares for this installation; a person outside any pod uses their own account.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        operation_name (str):
        body (InstallationOperationExecutionRequest): The deprecated org-level execute's body: an
            installation, maybe an account.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationExecutionResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_name=operation_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationOperationExecutionRequest,
) -> ErrorResponse | OperationExecutionResponse | None:
    """Execute Connector Operation (deprecated)

     Deprecated: use `pod.connector.operation.execute`. Still served, through the connectors the caller's
    pod declares for this installation; a person outside any pod uses their own account.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        operation_name (str):
        body (InstallationOperationExecutionRequest): The deprecated org-level execute's body: an
            installation, maybe an account.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationExecutionResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_name=operation_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationOperationExecutionRequest,
) -> Response[ErrorResponse | OperationExecutionResponse]:
    """Execute Connector Operation (deprecated)

     Deprecated: use `pod.connector.operation.execute`. Still served, through the connectors the caller's
    pod declares for this installation; a person outside any pod uses their own account.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        operation_name (str):
        body (InstallationOperationExecutionRequest): The deprecated org-level execute's body: an
            installation, maybe an account.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationExecutionResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        auth_config_name=auth_config_name,
        operation_name=operation_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    auth_config_name: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationOperationExecutionRequest,
) -> ErrorResponse | OperationExecutionResponse | None:
    """Execute Connector Operation (deprecated)

     Deprecated: use `pod.connector.operation.execute`. Still served, through the connectors the caller's
    pod declares for this installation; a person outside any pod uses their own account.

    Args:
        organization_id (UUID):
        auth_config_name (str):
        operation_name (str):
        body (InstallationOperationExecutionRequest): The deprecated org-level execute's body: an
            installation, maybe an account.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationExecutionResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            auth_config_name=auth_config_name,
            operation_name=operation_name,
            client=client,
            body=body,
        )
    ).parsed
