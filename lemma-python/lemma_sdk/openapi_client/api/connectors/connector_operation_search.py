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
    organization_id: UUID,
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
        "url": "/organizations/{organization_id}/connector-operations".format(
            organization_id=quote(str(organization_id), safe=""),
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
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[ErrorResponse | OperationDiscoverResponse]:
    """Search Connector Operations Across Installs

     Search operations across every connector installed in the org. Each hit carries the `auth_config` to
    execute it against, so a caller that knows what it wants to do — but not which connector provides it
    — needs one request instead of one per install.

    Args:
        organization_id (UUID):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationDiscoverResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        query=query,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> ErrorResponse | OperationDiscoverResponse | None:
    """Search Connector Operations Across Installs

     Search operations across every connector installed in the org. Each hit carries the `auth_config` to
    execute it against, so a caller that knows what it wants to do — but not which connector provides it
    — needs one request instead of one per install.

    Args:
        organization_id (UUID):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OperationDiscoverResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        query=query,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> Response[ErrorResponse | OperationDiscoverResponse]:
    """Search Connector Operations Across Installs

     Search operations across every connector installed in the org. Each hit carries the `auth_config` to
    execute it against, so a caller that knows what it wants to do — but not which connector provides it
    — needs one request instead of one per install.

    Args:
        organization_id (UUID):
        query (None | str | Unset):
        limit (int | Unset):  Default: 100.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OperationDiscoverResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        query=query,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    query: None | str | Unset = UNSET,
    limit: int | Unset = 100,
) -> ErrorResponse | OperationDiscoverResponse | None:
    """Search Connector Operations Across Installs

     Search operations across every connector installed in the org. Each hit carries the `auth_config` to
    execute it against, so a caller that knows what it wants to do — but not which connector provides it
    — needs one request instead of one per install.

    Args:
        organization_id (UUID):
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
            organization_id=organization_id,
            client=client,
            query=query,
            limit=limit,
        )
    ).parsed
