from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.operation_execution_request import OperationExecutionRequest
from ...models.operation_execution_response import OperationExecutionResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    body: OperationExecutionRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/connectors/{slug}/operations/{operation_name}/execute".format(
            pod_id=quote(str(pod_id), safe=""),
            slug=quote(str(slug), safe=""),
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
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OperationExecutionRequest,
) -> Response[ErrorResponse | OperationExecutionResponse]:
    """Execute Pod Connector Operation

     Run an operation through a pod connector. The account it runs as is decided by the connector: the
    caller's own for a PER_MEMBER connector, the shared one for a SHARED connector. Typed refusals:
    `CONNECTION_REQUIRED` and `REAUTH_REQUIRED` (with `connect_url`), `CONNECTOR_NEEDS_ACCOUNT`,
    `CONNECTOR_UNAVAILABLE`.

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):
        body (OperationExecutionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationExecutionResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OperationExecutionRequest,
) -> ErrorResponse | OperationExecutionResponse | None:
    """Execute Pod Connector Operation

     Run an operation through a pod connector. The account it runs as is decided by the connector: the
    caller's own for a PER_MEMBER connector, the shared one for a SHARED connector. Typed refusals:
    `CONNECTION_REQUIRED` and `REAUTH_REQUIRED` (with `connect_url`), `CONNECTOR_NEEDS_ACCOUNT`,
    `CONNECTOR_UNAVAILABLE`.

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):
        body (OperationExecutionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationExecutionResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OperationExecutionRequest,
) -> Response[ErrorResponse | OperationExecutionResponse]:
    """Execute Pod Connector Operation

     Run an operation through a pod connector. The account it runs as is decided by the connector: the
    caller's own for a PER_MEMBER connector, the shared one for a SHARED connector. Typed refusals:
    `CONNECTION_REQUIRED` and `REAUTH_REQUIRED` (with `connect_url`), `CONNECTOR_NEEDS_ACCOUNT`,
    `CONNECTOR_UNAVAILABLE`.

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):
        body (OperationExecutionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationExecutionResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: OperationExecutionRequest,
) -> ErrorResponse | OperationExecutionResponse | None:
    """Execute Pod Connector Operation

     Run an operation through a pod connector. The account it runs as is decided by the connector: the
    caller's own for a PER_MEMBER connector, the shared one for a SHARED connector. Typed refusals:
    `CONNECTION_REQUIRED` and `REAUTH_REQUIRED` (with `connect_url`), `CONNECTOR_NEEDS_ACCOUNT`,
    `CONNECTOR_UNAVAILABLE`.

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):
        body (OperationExecutionRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationExecutionResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            slug=slug,
            operation_name=operation_name,
            client=client,
            body=body,
        )
    ).parsed
