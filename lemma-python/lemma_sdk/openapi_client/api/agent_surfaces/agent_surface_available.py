from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.available_surfaces_response import AvailableSurfacesResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/available-surfaces".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AvailableSurfacesResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AvailableSurfacesResponse.from_dict(response.json())

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
) -> Response[AvailableSurfacesResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AvailableSurfacesResponse | ErrorResponse]:
    """List Available Surfaces

     The connectable-surface catalog: every surface platform with its connector,
    supported credential modes, and the schema to connect an account. Platform-
    level (no surface need exist); the pod scopes authorization only.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AvailableSurfacesResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> AvailableSurfacesResponse | ErrorResponse | None:
    """List Available Surfaces

     The connectable-surface catalog: every surface platform with its connector,
    supported credential modes, and the schema to connect an account. Platform-
    level (no surface need exist); the pod scopes authorization only.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AvailableSurfacesResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AvailableSurfacesResponse | ErrorResponse]:
    """List Available Surfaces

     The connectable-surface catalog: every surface platform with its connector,
    supported credential modes, and the schema to connect an account. Platform-
    level (no surface need exist); the pod scopes authorization only.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AvailableSurfacesResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> AvailableSurfacesResponse | ErrorResponse | None:
    """List Available Surfaces

     The connectable-surface catalog: every surface platform with its connector,
    supported credential modes, and the schema to connect an account. Platform-
    level (no surface need exist); the pod scopes authorization only.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AvailableSurfacesResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
        )
    ).parsed
