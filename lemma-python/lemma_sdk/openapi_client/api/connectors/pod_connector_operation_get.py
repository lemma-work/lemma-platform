from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.operation_detail import OperationDetail
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    slug: str,
    operation_name: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/connectors/{slug}/operations/{operation_name}".format(
            pod_id=quote(str(pod_id), safe=""),
            slug=quote(str(slug), safe=""),
            operation_name=quote(str(operation_name), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OperationDetail | None:
    if response.status_code == 200:
        response_200 = OperationDetail.from_dict(response.json())

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
) -> Response[ErrorResponse | OperationDetail]:
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
) -> Response[ErrorResponse | OperationDetail]:
    """Get Pod Connector Operation

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationDetail]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
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
) -> ErrorResponse | OperationDetail | None:
    """Get Pod Connector Operation

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationDetail
    """

    return sync_detailed(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | OperationDetail]:
    """Get Pod Connector Operation

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationDetail]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | OperationDetail | None:
    """Get Pod Connector Operation

    Args:
        pod_id (UUID):
        slug (str):
        operation_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationDetail
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            slug=slug,
            operation_name=operation_name,
            client=client,
        )
    ).parsed
