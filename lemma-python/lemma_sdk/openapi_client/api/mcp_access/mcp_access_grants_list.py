from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.connected_clients_response import ConnectedClientsResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    pod_id: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_pod_id: None | str | Unset
    if isinstance(pod_id, Unset):
        json_pod_id = UNSET
    elif isinstance(pod_id, UUID):
        json_pod_id = str(pod_id)
    else:
        json_pod_id = pod_id
    params["pod_id"] = json_pod_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/oauth/grants",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConnectedClientsResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ConnectedClientsResponse.from_dict(response.json())

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
) -> Response[ConnectedClientsResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
) -> Response[ConnectedClientsResponse | ErrorResponse]:
    """MCP clients you have connected

    Args:
        pod_id (None | Unset | UUID): Only this pod's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectedClientsResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
) -> ConnectedClientsResponse | ErrorResponse | None:
    """MCP clients you have connected

    Args:
        pod_id (None | Unset | UUID): Only this pod's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectedClientsResponse | ErrorResponse
    """

    return sync_detailed(
        client=client,
        pod_id=pod_id,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
) -> Response[ConnectedClientsResponse | ErrorResponse]:
    """MCP clients you have connected

    Args:
        pod_id (None | Unset | UUID): Only this pod's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConnectedClientsResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
) -> ConnectedClientsResponse | ErrorResponse | None:
    """MCP clients you have connected

    Args:
        pod_id (None | Unset | UUID): Only this pod's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConnectedClientsResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            pod_id=pod_id,
        )
    ).parsed
