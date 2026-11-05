from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.operation_discover_response import OperationDiscoverResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    slug: str,
    *,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_query: None | str | Unset
    if isinstance(query, Unset):
        json_query = UNSET
    else:
        json_query = query
    params["query"] = json_query

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/connectors/{slug}/operations".format(
            pod_id=quote(str(pod_id), safe=""),
            slug=quote(str(slug), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OperationDiscoverResponse | None:
    if response.status_code == 200:
        response_200 = OperationDiscoverResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OperationDiscoverResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[ErrorResponse | OperationDiscoverResponse]:
    """Discover Pod Connector Operations

    Args:
        pod_id (UUID):
        slug (str):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationDiscoverResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        query=query,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> ErrorResponse | OperationDiscoverResponse | None:
    """Discover Pod Connector Operations

    Args:
        pod_id (UUID):
        slug (str):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationDiscoverResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        slug=slug,
        client=client,
        query=query,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[ErrorResponse | OperationDiscoverResponse]:
    """Discover Pod Connector Operations

    Args:
        pod_id (UUID):
        slug (str):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationDiscoverResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        slug=slug,
        query=query,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    slug: str,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> ErrorResponse | OperationDiscoverResponse | None:
    """Discover Pod Connector Operations

    Args:
        pod_id (UUID):
        slug (str):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationDiscoverResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            slug=slug,
            client=client,
            query=query,
            limit=limit,
        )
    ).parsed
