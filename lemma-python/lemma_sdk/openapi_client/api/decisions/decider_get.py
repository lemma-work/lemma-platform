from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.decider_response import DeciderResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    decider_name: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/deciders/{decider_name}".format(
            pod_id=quote(str(pod_id), safe=""),
            decider_name=quote(str(decider_name), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DeciderResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DeciderResponse.from_dict(response.json())

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
) -> Response[DeciderResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeciderResponse | ErrorResponse]:
    """Get a decider

    Args:
        pod_id (UUID):
        decider_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeciderResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decider_name=decider_name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> DeciderResponse | ErrorResponse | None:
    """Get a decider

    Args:
        pod_id (UUID):
        decider_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeciderResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        decider_name=decider_name,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeciderResponse | ErrorResponse]:
    """Get a decider

    Args:
        pod_id (UUID):
        decider_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeciderResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decider_name=decider_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    decider_name: str,
    *,
    client: AuthenticatedClient | Client,
) -> DeciderResponse | ErrorResponse | None:
    """Get a decider

    Args:
        pod_id (UUID):
        decider_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeciderResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            decider_name=decider_name,
            client=client,
        )
    ).parsed
